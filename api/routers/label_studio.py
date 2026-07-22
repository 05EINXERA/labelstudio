import os
from fastapi import APIRouter, Depends, HTTPException

from label_studio_sdk import LabelStudio
from schemas import LabelStudioPayload
from api.auth import get_current_user

router = APIRouter(prefix="/api/label-studio", tags=["label-studio"], dependencies=[Depends(get_current_user)])

LABEL_STUDIO_URL = os.environ.get("LABEL_STUDIO_URL", "http://localhost:8000/")
LABEL_STUDIO_API_KEY = os.environ.get("LABEL_STUDIO_API_KEY", "")

@router.post("/send")
def send_to_ls(payload: LabelStudioPayload):
    if not LABEL_STUDIO_API_KEY:
        raise HTTPException(status_code=400, detail="Set LABEL_STUDIO_API_KEY before starting main.py.")
    if not payload.taskId and not payload.projectId:
        raise HTTPException(status_code=400, detail="Send projectId to create a task, or taskId to annotate an existing task.")
    if not payload.taskData:
        raise HTTPException(status_code=400, detail="Missing taskData.")
    if not payload.result:
        raise HTTPException(status_code=400, detail="Missing annotation result.")

    try:
        client = LabelStudio(
            base_url=LABEL_STUDIO_URL,
            api_key=LABEL_STUDIO_API_KEY,
        )

        task_id = payload.taskId
        if not task_id:
            task = client.tasks.create(data=payload.taskData, project=int(payload.projectId))
            task_id = str(task.id)

        annotation = client.annotations.create(
            int(task_id),
            result=payload.result,
            was_cancelled=False,
            ground_truth=False,
        )

        return {
            "taskId": task_id,
            "annotationId": annotation.id,
            "labelStudioUrl": LABEL_STUDIO_URL,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Label Studio sync failed.")
