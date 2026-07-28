from fastapi import APIRouter

from sourcetrace.modules.answers.router import router as answers_router
from sourcetrace.modules.conversations.router import router as conversations_router
from sourcetrace.modules.documents.router import router as documents_router
from sourcetrace.modules.health.router import router as health_router
from sourcetrace.modules.knowledge_bases.router import router as knowledge_bases_router

api_router = APIRouter()
api_router.include_router(health_router)

# Feature routers are added here as vertical slices become executable.
v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(knowledge_bases_router)
v1_router.include_router(documents_router)
v1_router.include_router(conversations_router)
v1_router.include_router(answers_router)
api_router.include_router(v1_router)
