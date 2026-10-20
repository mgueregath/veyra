from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str
    password: str


class MeOut(BaseModel):
    id: int
    username: str
    display_name: str | None
    is_superuser: bool


class ProjectOut(BaseModel):
    id: int
    slug: str
    name: str
    role: str
    config: dict
    n_frames: int = 0
    n_reviewed: int = 0


class BoxIn(BaseModel):
    id: int | None = None
    proposal_id: int | None = None
    bbox: list[int] = Field(min_length=4, max_length=4)
    label: str | None = None
    manual: bool = False
    orig_bbox: list[int] | None = None
    sources: list[str] = []
    score: float | None = None
    deleted: bool = False


class AnnotationIn(BaseModel):
    reviewed: bool = False
    boxes: list[BoxIn] = []


class BoxOut(BoxIn):
    pass


class AnnotationOut(BaseModel):
    reviewed: bool
    boxes: list[BoxOut]


class ProposalOut(BaseModel):
    id: int
    bbox: list[int]
    sources: list[str]
    score: float | None


class ReviewOut(BaseModel):
    reviewer: str
    final_user: str
    final_user_id: int
    note: str | None


class FrameOut(BaseModel):
    id: int
    position: int
    video: str
    video_id: int
    has_video: bool
    frame_index: int
    t: float | None
    width: int
    height: int
    stratum: str | None
    proposals: list[ProposalOut]
    annotation: AnnotationOut | None
    n_annotators: int
    status: str = "pending"
    n_reviewed: int = 0
    locked_by: str | None = None
    assigned_to_me: bool = False
    review: ReviewOut | None = None


class FrameItem(BaseModel):
    id: int
    position: int
    video: str
    frame_index: int
    t: float | None
    stratum: str | None
    reviewed: bool
    origin: str | None = None
    status: str = "pending"
    assigned: bool = False
    n_labeled: dict[str, int]


class ReviewIn(BaseModel):
    final_user_id: int
    note: str | None = None


class UserAnnotationOut(BaseModel):
    user_id: int
    user: str
    reviewed: bool
    boxes: list[BoxOut]


class AssignIn(BaseModel):
    users: list[str] = Field(min_length=1)
    frame_ids: list[int] | None = None  # si se omite: las imágenes sin asignar ni revisar
    overlap_pct: float = Field(
        0, ge=0, le=100
    )  # % de imágenes que se asignan además a una 2.ª persona (control de calidad)
    seed: int = 0


class QueueIn(BaseModel):
    scope: str = Field("assigned", pattern="^(assigned|any)$")


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    include: list[str] = ["final", "agreed", "single"]
    split: tuple[float, float, float] = (0.7, 0.15, 0.15)
    seed: str = "labelhub"


class ExportIn(BaseModel):
    format: str = Field("yolo", pattern="^(yolo|coco|both)$")
