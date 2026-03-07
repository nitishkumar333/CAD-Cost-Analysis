from abc import ABC, abstractmethod

class BaseTest(ABC):
    """
    Abstract base class for all DFM tests.
    """
    
    def __init__(self, config: dict):
        self.cfg = config
        
    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the DFM check."""
        pass
        
    @abstractmethod
    def analyze(self, solids: list, part=None) -> dict:
        """
        Perform the DFM analysis.
        
        Args:
            solids (list): List of cadquery.Solid objects representing bodies in the STEP file.
            part: The full cadquery.Workplane object (imported STEP), optional.
            
        Returns:
            dict: Standardized result with keys:
                  'check': Name of the check
                  'status': 'PASS', 'FAIL', 'WARNING', 'SKIP', or 'ERROR'
                  'message': Summary of result
                  'details': dict with any extra contextual data
        """
        pass
