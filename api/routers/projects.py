import json
import logging
import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, Query, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

import models
import schemas
from config import DATA_DIR, MAX_UPLOAD_FILES
from database import get_db, commit_with_retry
from schemas import ProjectModel, ProjectMetrics, ProjectSummary, ProjectTransferOwnership
from api.auth import get_current_user, require_csrf, get_current_annotator
from formats.common import measure_image

logger = logging.getLogger(__name__)


def get_user_accessible_team_ids(user: models.User, db: Session, annotator: Optional[models.TeamMember] = None) -> List[int]:
    """Return all team IDs where user is either a member or the creator."""
    names = {user.username}
    if annotator and annotator.name:
        names.add(annotator.name)

    member_teams = db.query(models.TeamMemberAssociation.team_id).filter(
        models.TeamMemberAssociation.member_name.in_(names)
    ).all()
    creator_teams = db.query(models.Team.id).filter(
        models.Team.creator.in_(names)
    ).all()
    return list(set([t[0] for t in member_teams] + [t[0] for t in creator_teams]))


def get_owned_project(project_id: int, user: models.User, db: Session, annotator: Optional[models.TeamMember] = None) -> models.Project:
    """Return the project if `user` owns it, created it, is in its team, or has tasks assigned, else raise 404.

    404 rather than 403 so the API does not confirm the existence of other
    users' project ids.
    """
    team_ids = get_user_accessible_team_ids(user, db, annotator)
    names = {user.username}
    if annotator and annotator.name:
        names.add(annotator.name)

    conditions = [
        models.Project.owner_id == user.id,
        models.Project.creator.in_(names),
    ]
    if team_ids:
        conditions.append(models.Project.team_id.in_(team_ids))

    # Also allow access if the user has an assigned task in this project
    task_pids = [
        t[0] for t in db.query(models.Task.project_id).filter(
            models.Task.project_id == project_id,
            models.Task.assignee.in_(names)
        ).all()
    ]
    if task_pids:
        conditions.append(models.Project.id.in_(task_pids))

    project = db.query(models.Project).filter(
        models.Project.id == project_id,
        or_(*conditions),
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def is_project_creator(project: models.Project, user: models.User, annotator: Optional[models.TeamMember] = None) -> bool:
    """Return True if user or annotator is the creator/owner of the project."""
    names = set()
    if annotator and annotator.name:
        names.add(annotator.name)
    else:
        names.add(user.username)
        
    if project.creator in names:
        return True
    if project.owner_id == user.id and (not annotator or annotator.name == user.username):
        return True
    return False



def _derive_status(total: int, completed: int) -> Optional[str]:
    """Project status implied by its task counts, or None if unchanged."""
    if total > 0 and completed == total:
        return "Completed"
    if completed > 0:
        return "In Progress"
    return None

def _aggregate_metrics(project_ids: List[int], db: Session) -> dict:
    """Task + label metrics for each id in `project_ids`.

    Two queries total regardless of how many projects are passed, so the list
    page does not fan out per row. Ids with no tasks still get a zeroed entry.
    """
    metrics = {
        pid: {"total": 0, "completed": 0, "in_progress": 0, "comments": 0,
              "progress": 0, "classes": 0, "total_time": 0, "avg_time_per_task": 0}
        for pid in project_ids
    }
    if not project_ids:
        return metrics

    task_stats = db.query(
        models.Task.project_id,
        models.Task.status,
        func.count(models.Task.id),
        func.coalesce(func.sum(models.Task.time_spent), 0),
    ).filter(
        models.Task.project_id.in_(project_ids)
    ).group_by(
        models.Task.project_id,
        models.Task.status,
    ).all()

    for pid, status, count, total_time in task_stats:
        if pid not in metrics:
            continue
        entry = metrics[pid]
        entry["total"] += count
        if status == 'Completed':
            entry["completed"] += count
        elif status == 'In Progress':
            entry["in_progress"] += count
        entry["total_time"] += total_time

    # Count comments using the Annotation table
    comment_counts = db.query(
        models.Task.project_id, func.count(models.Annotation.id)
    ).join(models.Annotation, models.Task.id == models.Annotation.task_id).filter(
        models.Task.project_id.in_(project_ids),
        models.Annotation.type == "comment"
    ).group_by(models.Task.project_id).all()
    
    for pid, count in comment_counts:
        metrics[pid]["comments"] = count

    label_counts = db.query(
        models.Label.project_id, func.count(models.Label.id),
    ).filter(models.Label.project_id.in_(project_ids)).group_by(models.Label.project_id).all()
    for pid, count in label_counts:
        metrics[pid]["classes"] = count

    for entry in metrics.values():
        total = entry["total"]
        if total > 0:
            entry["progress"] = int(entry["completed"] / total * 100)
            entry["avg_time_per_task"] = int(entry["total_time"] / total)

    return metrics


router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

@router.get("", response_model=List[ProjectSummary])
def get_projects(db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    """Every project the caller owns, created, or shares via a team/task assignment."""
    team_ids = get_user_accessible_team_ids(user, db, annotator)
    names = {user.username}
    if annotator and annotator.name:
        names.add(annotator.name)

    conditions = [
        models.Project.owner_id == user.id,
        models.Project.creator.in_(names),
    ]
    if team_ids:
        conditions.append(models.Project.team_id.in_(team_ids))

    task_pids = [
        t[0] for t in db.query(models.Task.project_id).filter(
            models.Task.assignee.in_(names)
        ).all()
    ]
    if task_pids:
        conditions.append(models.Project.id.in_(task_pids))

    query = db.query(models.Project, models.Team.name.label("team_name")).outerjoin(
        models.Team, models.Project.team_id == models.Team.id
    ).filter(or_(*conditions))
            
    projects_with_teams = query.order_by(models.Project.created_at.desc()).all()
    if not projects_with_teams:
        return []

    project_ids = [p.Project.id for p in projects_with_teams]
    metrics = _aggregate_metrics(project_ids, db)

    return [
        ProjectSummary(
            id=p.Project.id, name=p.Project.name, slug=p.Project.slug, type=p.Project.type, status=p.Project.status,
            creator=p.Project.creator, created_at=p.Project.created_at,
            team_id=p.Project.team_id, team_name=p.team_name,
            **metrics[p.Project.id],
        )
        for p in projects_with_teams
    ]

@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    p = get_owned_project(project_id, user, db, annotator)
    return {"id": p.id, "name": p.name, "slug": p.slug, "type": p.type, "status": p.status, "creator": p.creator, "created_at": p.created_at, "team_id": p.team_id, "is_owner": is_project_creator(p, user, annotator)}

@router.get("/{project_id}/metrics", response_model=ProjectMetrics)
def get_project_metrics(project_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    project = get_owned_project(project_id, user, db, annotator)
    m = _aggregate_metrics([project_id], db)[project_id]

    # This endpoint used to write the derived status back to the project, which
    # made a GET mutate the database (CLAUDE.md rule 4). The status is now
    # reported without being persisted; the write happens on task update.
    derived = _derive_status(m["total"], m["completed"])

    return ProjectMetrics(status=derived or project.status, **m)

@router.post("")
def create_project(project: ProjectModel, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    creator_name = annotator.name if annotator else user.username
    db_project = models.Project(name=project.name, slug=project.slug, type=project.type, status="Preparing", creator=creator_name, owner_id=user.id, team_id=project.team_id)
    db.add(db_project)
    commit_with_retry(db)
    db.refresh(db_project)
    return {"id": db_project.id, "status": "ok"}

def _apply_project_update(db_project: models.Project, project_update: schemas.ProjectUpdate) -> None:
    fields_set = getattr(project_update, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(project_update, "__fields_set__", set())
    if project_update.name is not None:
        db_project.name = project_update.name
        db_project.slug = project_update.name.lower().replace(" ", "-")
    if project_update.status is not None:
        db_project.status = project_update.status
    if "team_id" in fields_set or project_update.team_id is not None:
        db_project.team_id = project_update.team_id

@router.patch("/{project_id}")
def patch_project(project_id: int, project_update: schemas.ProjectUpdate, request: Request, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    db_project = get_owned_project(project_id, user, db, annotator)
    # X-Annotator-Name is used for UI filtering but is not a security boundary.
    _apply_project_update(db_project, project_update)
    commit_with_retry(db)
    return {"status": "ok"}

@router.delete("/{project_id}")
def delete_project(project_id: int, request: Request, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    db_project = get_owned_project(project_id, user, db, annotator)
    # X-Annotator-Name is used for UI filtering but is not a security boundary.
    db.query(models.Task).filter(models.Task.project_id == project_id).delete()
    db.query(models.Label).filter(models.Label.project_id == project_id).delete()
    db.delete(db_project)
    commit_with_retry(db)
    return {"status": "ok"}

@router.patch("/{project_id}/transfer-ownership")
@router.post("/{project_id}/transfer-ownership")
def transfer_project_ownership(
    project_id: int,
    payload: schemas.ProjectTransferOwnership,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator: Optional[models.TeamMember] = Depends(get_current_annotator),
):
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    caller_names = set()
    if annotator:
        caller_names.add(annotator.name)
    if user:
        caller_names.add(user.username)
    header_name = request.headers.get("X-Annotator-Name")
    if header_name:
        caller_names.add(header_name)

    is_owner = (project.creator in caller_names) or (project.owner_id == user.id)
    if not is_owner:
        raise HTTPException(status_code=403, detail="Only the project creator can transfer ownership")

    new_owner = payload.new_owner.strip()
    if not new_owner:
        raise HTTPException(status_code=400, detail="New owner name cannot be empty")

    if new_owner == project.creator or new_owner in caller_names:
        raise HTTPException(status_code=400, detail="User is already the project creator")

    target_user = db.query(models.User).filter(models.User.username == new_owner).first()
    target_member = db.query(models.TeamMember).filter(models.TeamMember.name == new_owner).first()
    if not target_user and not target_member:
        raise HTTPException(status_code=400, detail="Target user does not exist")

    if not target_member:
        target_member = models.TeamMember(name=new_owner, time_logged=0)
        db.add(target_member)

    if project.team_id:
        existing_assoc = db.query(models.TeamMemberAssociation).filter(
            models.TeamMemberAssociation.team_id == project.team_id,
            models.TeamMemberAssociation.member_name == new_owner,
        ).first()
        if not existing_assoc:
            assoc = models.TeamMemberAssociation(member_name=new_owner, team_id=project.team_id)
            db.add(assoc)

    project.creator = new_owner
    if target_user:
        project.owner_id = target_user.id

    commit_with_retry(db)
    db.refresh(project)
    return {"status": "ok", "id": project.id, "creator": project.creator}

ALLOWED_UPLOAD_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per image
_UPLOAD_CHUNK = 1024 * 1024


def _save_upload(f: UploadFile, uploads_dir: str) -> str:
    """Stream one upload to disk, enforcing the size cap. Returns the db path.

    Streamed in chunks rather than `f.file.read()` so a large file cannot be
    pulled into memory in full, and the cap is enforced while writing rather
    than after.
    """
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError(f"File type '{ext or 'unknown'}' is not allowed.")

    new_filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(uploads_dir, new_filename)

    written = 0
    try:
        with open(filepath, "wb") as out_file:
            while True:
                chunk = f.file.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise ValueError(f"File exceeds the 25 MB limit.")
                out_file.write(chunk)
    except Exception:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        raise

    if written == 0:
        try:
            os.remove(filepath)
        except OSError as exc:
            logger.warning("Could not remove empty upload %s: %s", filepath, exc)
        raise ValueError("File is empty.")

    # Always a forward slash: this is served as a URL path, and os.path.join
    # would produce a backslash on Windows that breaks the <img src>.
    return f"uploads/{new_filename}"


@router.post("/{project_id}/upload")
def upload_files(
    project_id: int,
    assignee: Optional[str] = Query(None),
    skip_duplicates: bool = Query(False),
    file: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator: Optional[models.TeamMember] = Depends(get_current_annotator),
):
    """Bulk image upload. One bad file no longer aborts the whole batch.

    Previously any disallowed extension raised mid-loop, so earlier files were
    left on disk with no task row and the client got a 400 with no record of
    what did succeed. Each file is now reported individually.
    Supports skip_duplicates=True to bypass files that already exist in this project.
    """
    db_project = get_owned_project(project_id, user, db, annotator)
    if not is_project_creator(db_project, user, annotator):
        raise HTTPException(status_code=403, detail="Only the project creator can upload tasks to this project.")

    # Bounds the work one request can queue. Each file is streamed to disk while
    # holding a worker thread, so an unbounded batch lets a single request stall
    # the shared threadpool for everyone else on the instance.
    if len(file) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{len(file)} files in one request; the limit is "
                f"{MAX_UPLOAD_FILES}. Upload in smaller batches."
            ),
        )

    uploads_dir = os.path.join(DATA_DIR, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    existing_filenames = set()
    if skip_duplicates:
        existing_filenames = {
            d[0]
            for d in db.query(models.Task.description).filter(models.Task.project_id == project_id).all()
            if d[0]
        }

    uploaded = []
    failed = []
    skipped = []
    seen_in_batch = set()

    for f in file:
        if skip_duplicates and (f.filename in existing_filenames or f.filename in seen_in_batch):
            skipped.append({"filename": f.filename, "reason": "Already exists in project"})
            continue
        if f.filename:
            seen_in_batch.add(f.filename)

        try:
            db_filepath = _save_upload(f, uploads_dir)
        except ValueError as exc:
            failed.append({"filename": f.filename, "error": str(exc)})
            continue
        except OSError as exc:
            logger.error("Failed to write upload %s: %s", f.filename, exc)
            failed.append({"filename": f.filename, "error": "Could not save the file."})
            continue

        # Measured once here rather than on every export: YOLO normalizes by
        # these and mask rasterization sizes its canvas from them. Pillow reads
        # only the header, so this adds no meaningful cost to a request that
        # already wrote the file.
        width, height = measure_image(os.path.join(DATA_DIR, *db_filepath.split("/")))

        db.add(models.Task(
            project_id=project_id, image_path=db_filepath,
            description=f.filename, status='New', assignee=assignee,
            image_width=width, image_height=height,
        ))
        uploaded.append({"filename": f.filename, "path": db_filepath})

    commit_with_retry(db)
    return {
        "status": "ok",
        "uploaded": uploaded,
        "failed": failed,
        "skipped": skipped,
        # Legacy field: project_details.js only checked res.ok, but keep the
        # shape until that page is deleted (tracker P5.1).
        "files": [u["path"] for u in uploaded],
    }
