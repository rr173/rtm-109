from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.schemas import (
    EfficiencyStatsRequest, EfficiencyStatsResponse,
    DeviceEfficiency, DeviceTypeEfficiency, IdlePeriod,
    BottleneckPredictionRequest, BottleneckPredictionResponse,
    HighRiskDeviceType, FailedSimulatedOrder, DeviceRecommendation,
    SimulatedOrderResult, SimulatedScheduleEntry
)
from app.scheduler import calculate_efficiency_stats, predict_bottlenecks

router = APIRouter(prefix="/efficiency", tags=["efficiency"])


@router.post("/stats", response_model=EfficiencyStatsResponse)
def get_efficiency_stats(request: EfficiencyStatsRequest, db: Session = Depends(get_db)):
    if request.start_time >= request.end_time:
        raise HTTPException(status_code=400, detail="start_time 必须早于 end_time")

    result = calculate_efficiency_stats(db, request.start_time, request.end_time)

    if not result.get("success", False):
        raise HTTPException(status_code=400, detail=result.get("message", "统计失败"))

    device_efficiencies = []
    for dev in result["device_efficiencies"]:
        idle_periods = [
            IdlePeriod(**ip) for ip in dev["idle_periods"]
        ]
        device_efficiencies.append(DeviceEfficiency(
            device_id=dev["device_id"],
            device_name=dev["device_name"],
            device_type=dev["device_type"],
            utilization_rate=dev["utilization_rate"],
            scheduled_minutes=dev["scheduled_minutes"],
            available_minutes=dev["available_minutes"],
            idle_periods=idle_periods,
            avg_waiting_time_minutes=dev["avg_waiting_time_minutes"]
        ))

    device_type_efficiencies = []
    for dtype in result["device_type_efficiencies"]:
        type_devices = []
        for dev in dtype["devices"]:
            idle_periods = [
                IdlePeriod(**ip) for ip in dev["idle_periods"]
            ]
            type_devices.append(DeviceEfficiency(
                device_id=dev["device_id"],
                device_name=dev["device_name"],
                device_type=dev["device_type"],
                utilization_rate=dev["utilization_rate"],
                scheduled_minutes=dev["scheduled_minutes"],
                available_minutes=dev["available_minutes"],
                idle_periods=idle_periods,
                avg_waiting_time_minutes=dev["avg_waiting_time_minutes"]
            ))
        device_type_efficiencies.append(DeviceTypeEfficiency(
            device_type=dtype["device_type"],
            device_count=dtype["device_count"],
            avg_utilization_rate=dtype["avg_utilization_rate"],
            max_utilization_diff=dtype["max_utilization_diff"],
            devices=type_devices if request.group_by_type else []
        ))

    return EfficiencyStatsResponse(
        start_time=result["start_time"],
        end_time=result["end_time"],
        total_devices=result["total_devices"],
        device_efficiencies=device_efficiencies,
        device_type_efficiencies=device_type_efficiencies
    )


@router.post("/bottleneck-prediction", response_model=BottleneckPredictionResponse)
def get_bottleneck_prediction(request: BottleneckPredictionRequest, db: Session = Depends(get_db)):
    if len(request.simulated_orders) > 50:
        raise HTTPException(status_code=400, detail="模拟工单不能超过50条")

    simulated_orders_dicts = [
        {
            "product_name": o.product_name,
            "quantity": o.quantity,
            "expected_start_time": o.expected_start_time
        }
        for o in request.simulated_orders
    ]

    result = predict_bottlenecks(db, request.future_days, simulated_orders_dicts)

    if not result.get("success", False):
        raise HTTPException(status_code=400, detail=result.get("message", "预测失败"))

    high_risk = [
        HighRiskDeviceType(**hr) for hr in result["high_risk_device_types"]
    ]

    failed = [
        FailedSimulatedOrder(**fo) for fo in result["failed_orders"]
    ]

    recommendations = [
        DeviceRecommendation(**dr) for dr in result["device_recommendations"]
    ]

    simulated_results = []
    for sr in result["simulated_results"]:
        entries = [
            SimulatedScheduleEntry(**e) for e in sr["schedule_entries"]
        ]
        simulated_results.append(SimulatedOrderResult(
            product_name=sr["product_name"],
            quantity=sr["quantity"],
            expected_start_time=sr["expected_start_time"],
            scheduled=sr["scheduled"],
            schedule_entries=entries,
            failure_reason=sr.get("failure_reason"),
            bottleneck_step=sr.get("bottleneck_step")
        ))

    return BottleneckPredictionResponse(
        future_days=result["future_days"],
        total_simulated_orders=result["total_simulated_orders"],
        high_risk_device_types=high_risk,
        failed_orders=failed,
        device_recommendations=recommendations,
        simulated_results=simulated_results
    )
