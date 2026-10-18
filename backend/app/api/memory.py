from fastapi import APIRouter, Request, Response
from app.schemas.memory import PreferenceResponse, PreferenceWrite

router = APIRouter(prefix="/api/memory", tags=["Preferences"])


@router.get("", response_model=PreferenceResponse)
def get_preferences(request: Request):
    return request.app.state.preferences.get()


@router.put("/{key}", response_model=PreferenceResponse)
def set_preference(key: str, body: PreferenceWrite, request: Request):
    return request.app.state.preferences.set(key, body.value)


@router.delete("/{key}", status_code=204)
def delete_preference(key: str, request: Request):
    request.app.state.preferences.delete(key)
    return Response(status_code=204)
