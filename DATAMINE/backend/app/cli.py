"""Secure one-time local administrator bootstrap."""
import argparse
import asyncio
from getpass import getpass

from sqlalchemy import func, select

from app.core.auth import hash_password
from app.db.session import AsyncSessionLocal, engine
from app.models.identity import User


async def create_admin() -> None:
    username = input("Admin username: ").strip()
    email = input("Admin email: ").strip()
    password = getpass("Admin password (12+ chars): ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    encoded = hash_password(password)
    async with AsyncSessionLocal() as session:
        if await session.scalar(select(func.count()).select_from(User).where(User.role == "ADMIN")):
            raise SystemExit("An administrator already exists; use authenticated user provisioning")
        if await session.scalar(select(User.id).where((User.username == username) | (User.email == email))):
            raise SystemExit("Username or email is already registered")
        session.add(User(username=username, email=email, password_hash=encoded, role="ADMIN"))
        await session.commit()
    print("Administrator created.")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create-admin"])
    command = parser.parse_args().command
    if command == "create-admin":
        asyncio.run(create_admin())
