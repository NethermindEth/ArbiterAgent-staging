from pydantic import BaseModel, Field
from typing import Optional, List

class QAPair(BaseModel):
    """Model for a question-answer pair."""
    question: str
    answer: str

class TaskResponse(BaseModel):
    """Response model for task creation."""
    id: str
    taskId: str
    projectRepo: Optional[str] = None
    title: str
    description: str
    bounty: Optional[str] = None
    status: str
    startTime: Optional[str] = None
    deadline: Optional[str] = None
    selectedBranch: Optional[str] = None
    selectedFiles: Optional[List[str]] = []
    selectedDocs: Optional[List[str]] = []
    additionalLinks: Optional[List[str]] = []
    additionalDocs: Optional[str] = None
    qaResponses: Optional[List[QAPair]] = []

class TaskCache(BaseModel):
    """Model representing the task cache structure."""
    taskId: Optional[str] = None
    selectedFilesContent: Optional[str] = None
    selectedDocsContent: Optional[str] = None
    additionalLinks: Optional[List[str]] = []
    additionalDocs: Optional[str] = None
    qaResponses: Optional[List[QAPair]] = []