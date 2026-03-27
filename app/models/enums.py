from enum import Enum


class UserRole(str, Enum):
    """
    Enumeration of user roles within the system.

    Defines access levels from Administrative (OWNER) to Operational (WORKER).
    """

    OWNER = "owner"
    MANAGER = "manager"
    SALES = "sales"
    ACHARI = "achari"
    WORKER = "worker"
