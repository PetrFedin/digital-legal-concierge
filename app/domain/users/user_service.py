from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
class UserService:
    def __init__(self, db:AsyncSession): self.db=db
    async def get_or_create_from_telegram(self, *, telegram_id:int, telegram_username:str|None=None, full_name:str|None=None):
        res=await self.db.execute(select(User).where(User.telegram_id==telegram_id)); user=res.scalars().first()
        if user:
            user.telegram_username=telegram_username or user.telegram_username; user.full_name=full_name or user.full_name; await self.db.flush(); return user
        user=User(telegram_id=telegram_id,telegram_username=telegram_username,full_name=full_name); self.db.add(user); await self.db.flush(); return user
