from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Time
from sqlalchemy.orm import relationship
from app.database import Base
import datetime


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    device_type = Column(String, index=True, nullable=False)
    daily_start = Column(String, nullable=False, default="08:00")
    daily_end = Column(String, nullable=False, default="20:00")

    schedule_entries = relationship("ScheduleEntry", back_populates="device")
    maintenance_plans = relationship("MaintenancePlan", back_populates="device", cascade="all, delete-orphan")


class MaintenancePlan(Base):
    __tablename__ = "maintenance_plans"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    description = Column(String, nullable=True)

    device = relationship("Device", back_populates="maintenance_plans")


class ProcessRoute(Base):
    __tablename__ = "process_routes"

    id = Column(Integer, primary_key=True, index=True)
    product_name = Column(String, unique=True, index=True, nullable=False)

    steps = relationship("ProcessStep", back_populates="route", order_by="ProcessStep.step_order")


class ProcessStep(Base):
    __tablename__ = "process_steps"

    id = Column(Integer, primary_key=True, index=True)
    route_id = Column(Integer, ForeignKey("process_routes.id"), nullable=False)
    step_order = Column(Integer, nullable=False)
    step_name = Column(String, nullable=False)
    device_type = Column(String, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    min_gap_after = Column(Integer, default=0)

    route = relationship("ProcessRoute", back_populates="steps")


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String, unique=True, index=True, nullable=False)
    product_name = Column(String, nullable=False)
    expected_start_time = Column(DateTime, nullable=False)
    deadline = Column(DateTime, nullable=False)
    status = Column(String, default="pending")
    is_locked = Column(Boolean, default=False)
    bottleneck_step = Column(String, nullable=True)

    schedule_entries = relationship("ScheduleEntry", back_populates="order", cascade="all, delete-orphan")


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    step_id = Column(Integer, ForeignKey("process_steps.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    step_order = Column(Integer, nullable=False)
    step_name = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)

    order = relationship("WorkOrder", back_populates="schedule_entries")
    device = relationship("Device", back_populates="schedule_entries")


class ConflictRecord(Base):
    __tablename__ = "conflict_records"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    conflict_type = Column(String, nullable=False)
    description = Column(String, nullable=False)
    detected_at = Column(DateTime, default=datetime.datetime.utcnow)
