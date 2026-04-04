# tools — Tool Layer (Слой 5) + Capability Discovery (Слой 35)
# Инструменты: терминал, файловая система, Python runtime,
# GitHub API, Docker, базы данных, облачные API, поисковые системы.
# Автоматическое обнаружение новых библиотек и API.
from .tool_layer import (
    ToolLayer,
    BaseTool,
    TerminalTool,
    FileSystemTool,
    PythonRuntimeTool,
    SearchTool,
    GitHubTool,
    DockerTool,
    DatabaseTool,
    PackageManagerTool,
    CloudAPITool,
    build_tool_layer,
)
from .capability_discovery import CapabilityDiscovery, DiscoveredCapability
from .browser_tool import BrowserTool, BrowserPage
from .tool_broker import (
    ToolBroker, ToolRequest, ToolResponse,
    BrokerError, CapabilityDeniedError, ApprovalRequiredError, ProhibitedActionError,
)

__all__ = [
    'ToolLayer', 'BaseTool',
    'TerminalTool', 'FileSystemTool', 'PythonRuntimeTool',
    'SearchTool', 'GitHubTool', 'DockerTool',
    'DatabaseTool', 'PackageManagerTool', 'CloudAPITool',
    'build_tool_layer',
    'CapabilityDiscovery', 'DiscoveredCapability',
    'BrowserTool', 'BrowserPage',
    'ToolBroker', 'ToolRequest', 'ToolResponse',
    'BrokerError', 'CapabilityDeniedError', 'ApprovalRequiredError', 'ProhibitedActionError',
]
