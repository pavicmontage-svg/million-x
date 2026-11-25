"""The six analytical roles. They are deterministic, auditable components (not LLMs); each has one
clearly separated responsibility. The Risk Manager lives in ``quanttitan.risk.manager``."""
from ..risk.manager import RiskManager
from .execution_specialist import ExecutionSpecialist
from .market_analyst import MarketAnalyst
from .performance_auditor import PerformanceAuditor
from .portfolio_manager import PortfolioManager
from .quant_researcher import QuantResearcher

AGENT_ORDER = ["quant_researcher", "market_analyst", "execution_specialist", "portfolio_manager", "risk_manager",
               "performance_auditor"]

__all__ = ["RiskManager", "ExecutionSpecialist", "MarketAnalyst", "PerformanceAuditor", "PortfolioManager",
           "QuantResearcher", "AGENT_ORDER"]
