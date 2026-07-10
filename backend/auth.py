import os
import time
from pathlib import Path

import jwt
from dotenv import load_dotenv
from fastapi import Header, HTTPException

import credits

load_dotenv(Path(__file__).parent / ".env")

CLERK_JWKS_URL = os.environ["CLERK_JWKS_URL"]

# PyJWKClient caches keys in memory and re-fetches automatically when a kid
# it hasn't seen shows up (e.g. after Clerk rotates signing keys) -- no manual
# cache invalidation needed.
_jwks_client = jwt.PyJWKClient(CLERK_JWKS_URL)

# Timestamp of the last successfully authenticated request, used by main.py's
# idle auto-stop task to decide when the RunPod pod has gone unused. Every
# route that depends on get_current_user counts as activity -- notably this
# includes the background queue poller (GenerationActivityContext.tsx), so a
# tab left open keeps resetting this even with no active generation. That's a
# deliberate tradeoff (see main.py's idle-stop loop for the full explanation).
_last_activity_at = time.time()


def get_last_activity() -> float:
    return _last_activity_at


def _verify_token(token: str) -> dict:
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid auth token: {e}")


async def get_current_user(authorization: str = Header(...)) -> str:
    """FastAPI dependency: verifies the Clerk JWT from the
    `Authorization: Bearer <token>` header, provisions/refreshes the user's
    users.json record (first-login creation + lazy monthly credit reset),
    and returns the Clerk user_id (the `sub` claim)."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header")
    token = authorization.removeprefix("Bearer ").strip()

    claims = _verify_token(token)
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(401, "Token missing sub claim")

    # Clerk's default session token doesn't include email unless a custom
    # claim was configured in the Clerk dashboard -- fall back to None rather
    # than failing, since email is informational here (users.json is keyed by
    # user_id, not email).
    email = claims.get("email") or claims.get("email_address")

    credits.get_or_create_user(user_id, email)

    global _last_activity_at
    _last_activity_at = time.time()

    return user_id
