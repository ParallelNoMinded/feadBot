from pydantic import BaseModel


class AdminAccount(BaseModel):
    telegram_user_id: str
    role: str
