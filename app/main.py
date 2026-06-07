from fastapi import FastAPI
from app.database import engine, Base
from app.routers import api_router
import os

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="工艺路线排产与冲突检测服务",
    description="车间生产排产系统，支持设备管理、工艺路线定义、工单排产、冲突检测等功能",
    version="1.0.0",
)

app.include_router(api_router, prefix="/api")


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "scheduling-service"}
