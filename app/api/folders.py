from fastapi import APIRouter, Depends, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import Principal, get_principal
from app.db import get_db
from app.errors import AppError, Conflict, NotFound
from app.models import Folder, Video
from app.schemas import FolderCreate, FolderList, FolderOut

router = APIRouter(prefix="/v1/folders", tags=["folders"])


def load_folder(db: Session, folder_id: str, principal: Principal) -> Folder:
    folder = db.get(Folder, folder_id)
    if folder is None or not principal.can_access(folder.owner):
        raise NotFound("Folder not found")
    return folder


def load_folder_for_video(db: Session, folder_id: str, video_owner: str, principal: Principal) -> Folder:
    folder = load_folder(db, folder_id, principal)
    if folder.owner != video_owner:
        raise AppError("Folder belongs to another owner", status_code=422, code="folder_owner_mismatch")
    return folder


def folder_out(folder: Folder, principal: Principal) -> FolderOut:
    return FolderOut(id=folder.id, name=folder.name, owner=folder.owner, mine=folder.owner == principal.name)


@router.post("", status_code=201, response_model=FolderOut)
def create_folder(
    body: FolderCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    folder = Folder(owner=principal.name, name=body.name)
    db.add(folder)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict(f"A folder named '{body.name}' already exists", code="folder_exists") from exc
    return folder_out(folder, principal)


@router.get("", response_model=FolderList)
def list_folders(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    query = select(Folder)
    if not principal.is_admin:
        query = query.where(Folder.owner == principal.name)
    items = db.scalars(query.order_by(Folder.name, Folder.owner)).all()
    return FolderList(items=[folder_out(f, principal) for f in items])


@router.delete("/{folder_id}", status_code=204)
def delete_folder(
    folder_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    folder = load_folder(db, folder_id, principal)
    # The videos stay; they only leave the folder.
    db.execute(update(Video).where(Video.folder_id == folder.id).values(folder_id=None))
    db.delete(folder)
    db.commit()
    return Response(status_code=204)
