from pydantic import BaseModel


class Token(BaseModel):
    """
    Schema for JWT access tokens and authentication metadata.
    """

    access_token: str
    token_type: str
