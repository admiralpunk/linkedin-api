"""Pydantic response models — the public JSON contract of this API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Name(BaseModel):
    first: str | None = None
    last: str | None = None
    full: str | None = None


class Experience(BaseModel):
    title: str | None = None
    company: str | None = None
    company_url: str | None = None
    location: str | None = None
    start: str | None = None  # "YYYY-MM" or "YYYY"
    end: str | None = None  # None => present
    description: str | None = None


class Education(BaseModel):
    school: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    start: str | None = None
    end: str | None = None


class Certification(BaseModel):
    name: str | None = None
    authority: str | None = None
    license_number: str | None = None
    url: str | None = None
    start: str | None = None
    end: str | None = None


class Language(BaseModel):
    name: str | None = None
    proficiency: str | None = None


class Images(BaseModel):
    profile_picture_url: str | None = None
    background_url: str | None = None


class Profile(BaseModel):
    """Structured LinkedIn profile — the successful response body."""

    public_id: str
    profile_url: str
    name: Name
    headline: str | None = None
    location: str | None = None
    industry: str | None = None
    about: str | None = None
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    images: Images = Field(default_factory=Images)
    fetched_at: str
    cached: bool = False


class ProfileRequest(BaseModel):
    """POST body for /api/v1/profile."""

    url: str


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: str
    cookies_present: bool
    version: str
