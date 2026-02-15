from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from bot.database.database import Base


class Company(Base):
    __tablename__ = "companies"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # название компании / салона
    address = Column(String, nullable=False)  # адрес
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    appointments = relationship("Appointment", back_populates="company")


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
    phone = Column(String, nullable=False, default="", index=True)  # "" до отправки контакта
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    appointments = relationship("Appointment", back_populates="user")


class Appointment(Base):
    __tablename__ = "appointments"
    
    id = Column(Integer, primary_key=True, index=True)
    dikidi_id = Column(Integer, unique=True, nullable=False, index=True)  # авто в БД: 1, 2, 3, ...
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    
    event = Column(String, nullable=False)  # название услуги/события
    date = Column(String, nullable=False)  # дата записи
    time = Column(String, nullable=False)  # время записи
    master = Column(String, nullable=False)  # мастер
    clientlink = Column(String, nullable=False)  # ссылка на запись
    visit_status = Column(String, nullable=True)  # Визит завершен / Ожидает визита (из Dikidi .journal458-visit-status)

    status = Column(String, nullable=False, default="created")  # created / changed / canceled / visited / active
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    user = relationship("User", back_populates="appointments")
    company = relationship("Company", back_populates="appointments")
    notifications = relationship("Notification", back_populates="appointment")


class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=False)
    type = Column(String, nullable=False)  # created / changed / canceled / reminder / after_visit / confirmation
    send_at = Column(DateTime(timezone=True), nullable=False)
    sent = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    appointment = relationship("Appointment", back_populates="notifications")
