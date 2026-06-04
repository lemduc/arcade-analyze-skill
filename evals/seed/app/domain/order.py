"""Domain layer — pure business entities. Depends on nothing else."""
from dataclasses import dataclass


@dataclass
class Order:
    id: int
    user_id: int
    total: float
