from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from api.auth import (ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token,
                      get_password_hash, verify_password)
from database import get_db
from schemas import Token, UserCreate

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=Token)
def register(user: UserCreate, response: Response, db: Session = Depends(get_db)):
    db_user = (
        db.query(models.User).filter(models.User.username == user.username).first()
    )
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")

    hashed_password = get_password_hash(user.password)
    new_user = models.User(username=user.username, hashed_password=hashed_password)
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Username already registered")

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": new_user.username}, expires_delta=access_token_expires
    )
    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        samesite="lax",
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/token", response_model=Token)
def login_for_access_token(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = (
        db.query(models.User).filter(models.User.username == form_data.username).first()
    )
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        samesite="lax",
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"status": "ok"}
