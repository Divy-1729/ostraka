from typing import Literal
from pydantic import BaseModel, Field


class Node(BaseModel):
    label: str = Field(max_length=100)
    detail: str = Field(default="", max_length=350)


class Diagram(BaseModel):
    kind: Literal["flow", "compare", "cycle"] = "flow"
    title: str = Field(max_length=150)
    nodes: list[Node] = Field(min_length=2, max_length=6)
    caption: str = Field(max_length=600)


class Chapter(BaseModel):
    title: str = Field(max_length=180)
    summary: str = Field(max_length=2500)
    takeaways: list[str] = Field(min_length=1, max_length=5)
    glossary: dict[str, str] = Field(default_factory=dict)
    diagram: Diagram
    caveat: str = Field(max_length=1000)


class Slide(BaseModel):
    title: str = Field(max_length=180)
    message: str = Field(max_length=500)
    bullets: list[str] = Field(min_length=1, max_length=4)
    diagram: Diagram | None = None
    notes: str = Field(max_length=4000)
    pages: list[int] = Field(min_length=1)


class Deck(BaseModel):
    title: str = Field(max_length=200)
    slides: list[Slide] = Field(min_length=5, max_length=20)


class Annotation(BaseModel):
    page: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(max_length=15000)
    note: str = Field(default="", max_length=15000)
    color: Literal["yellow", "mint", "rose"] = "yellow"


class PresentationRequest(BaseModel):
    minutes: int = Field(default=10, ge=3, le=30)
    audience: str = Field(default="Curious technical teammates", max_length=500)
