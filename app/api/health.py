from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/")
def health_check():
    """
    Perform a basic health check of the API.

    Returns:
        dict: Status OK message.
    """
    return {"status": "ok"}
