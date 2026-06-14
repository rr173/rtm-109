from fastapi import APIRouter
from app.routers import devices, routes, orders, schedule, maintenance, inventory, efficiency, fault_management, fixtures, scenarios, changeover

api_router = APIRouter()
api_router.include_router(devices.router)
api_router.include_router(routes.router)
api_router.include_router(orders.router)
api_router.include_router(schedule.router)
api_router.include_router(maintenance.router)
api_router.include_router(inventory.router)
api_router.include_router(efficiency.router)
api_router.include_router(fault_management.router)
api_router.include_router(fixtures.router)
api_router.include_router(scenarios.router)
api_router.include_router(changeover.router)
