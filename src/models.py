"""
Data models for CodeBuddy2API
"""
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field

from config import DEFAULT_CODEBUDDY_MODELS


class Message(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]  # Support both string and complex list format


class ChatCompletionRequest(BaseModel):
    model: str = DEFAULT_CODEBUDDY_MODELS[0]
    messages: List[Message]
    stream: bool = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    top_p: Optional[float] = 1.0
    frequency_penalty: Optional[float] = 0.0
    presence_penalty: Optional[float] = 0.0
    stop: Optional[Union[str, List[str]]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: Message
    finish_reason: Optional[str] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: Optional[Dict[str, int]] = None


class Model(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "codebuddy"


class ModelList(BaseModel):
    object: str = "list"
    data: List[Model]


class CredentialInfo(BaseModel):
    """凭证信息"""
    index: int
    user_id: str
    created_at: int
    has_token: bool
