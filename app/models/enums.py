from enum import Enum


class UserRole(str, Enum):
    OWNER = "owner"
    MANAGER = "manager"
    SALES = "sales"
    ACHARI = "achari"
    WORKER = "worker"
