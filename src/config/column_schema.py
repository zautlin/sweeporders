"""
Column Schema System

Centralized column name accessor to make the pipeline schema-independent.
All column names are defined in config.COLUMN_MAPPING and accessed through
this module to support different production dataset schemas.

Usage:
    from column_schema import col
    
    # Access column names
    df[col.orders.order_id]
    df[col.trades.trade_time]
    df[col.metrics.exec_cost_arrival]

Benefits:
    - Single source of truth in config.py
    - Change config → entire pipeline adapts
    - Production-ready: supports any dataset schema
    - Clear, readable code with IDE autocomplete
"""

import config
from typing import Dict, Any


class ColumnAccessor:
    """
    Accessor class for column names of a specific data type.
    
    Provides attribute-based access to column names defined in config.COLUMN_MAPPING.
    
    Example:
        accessor = ColumnAccessor('orders', config.COLUMN_MAPPING['orders'])
        accessor.order_id  # Returns 'orderid' (or whatever is in config)
    """
    
    def __init__(self, data_type: str, mapping: Dict[str, str]):
        """Initialize column accessor."""
        self._data_type = data_type
        self._mapping = mapping
        
    def __getattr__(self, name: str) -> str:
        """Get actual column name for a standard column name."""
        if name.startswith('_'):
            # Internal attributes
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        
        if name not in self._mapping:
            raise AttributeError(
                f"Column '{name}' not found in {self._data_type} mapping.\n"
                f"Available columns: {', '.join(sorted(self._mapping.keys()))}\n"
                f"Please add '{name}' to config.COLUMN_MAPPING['{self._data_type}']"
            )
        
        return self._mapping[name]
    
    def __dir__(self):
        """Return list of available column names for IDE autocomplete."""
        return sorted(self._mapping.keys())
    
    def get(self, name: str, default: str = None) -> str:
        """Get column name with optional default."""
        return self._mapping.get(name, default)
    
    def has(self, name: str) -> bool:
        """Check if column exists in mapping."""
        return name in self._mapping
    
    def all(self) -> Dict[str, str]:
        """
        Get all column mappings.
        
        Returns:
            Dictionary of all column mappings
        """
        return self._mapping.copy()


class ColumnSchema:
    """
    Main column schema system.
    
    Provides centralized access to all column names across different data types.
    Loads mappings from config.COLUMN_MAPPING.
    
    Usage:
        schema = ColumnSchema()
        schema.orders.order_id      # Returns 'orderid'
        schema.trades.trade_price   # Returns 'tradeprice'
        schema.metrics.vwap         # Returns 'vwap'
    """
    
    def __init__(self):
        """Initialize column schema from config.COLUMN_MAPPING."""
        self._accessors = {}
        self._load_from_config()
    
    def _load_from_config(self):
        """Load column mappings from config.COLUMN_MAPPING."""
        if not hasattr(config, 'COLUMN_MAPPING'):
            raise ValueError(
                "config.COLUMN_MAPPING not found. "
                "Please define COLUMN_MAPPING in config.py"
            )
        
        column_mapping = config.COLUMN_MAPPING
        
        if not isinstance(column_mapping, dict):
            raise ValueError(
                f"config.COLUMN_MAPPING must be a dict, got {type(column_mapping)}"
            )
        
        # Create accessor for each data type
        for data_type, mapping in column_mapping.items():
            if not isinstance(mapping, dict):
                raise ValueError(
                    f"config.COLUMN_MAPPING['{data_type}'] must be a dict, "
                    f"got {type(mapping)}"
                )
            
            self._accessors[data_type] = ColumnAccessor(data_type, mapping)
    
    def __getattr__(self, name: str) -> ColumnAccessor:
        """Get column accessor for a data type."""
        if name.startswith('_'):
            # Internal attributes
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        
        if name not in self._accessors:
            raise AttributeError(
                f"Data type '{name}' not found in column mapping.\n"
                f"Available data types: {', '.join(sorted(self._accessors.keys()))}\n"
                f"Please add '{name}' to config.COLUMN_MAPPING"
            )
        
        return self._accessors[name]
    
    def __dir__(self):
        """Return list of available data types for IDE autocomplete."""
        return sorted(self._accessors.keys())
    
    def get_accessor(self, data_type: str) -> ColumnAccessor:
        """Get column accessor for a data type."""
        return self._accessors.get(data_type)
    
    def has_data_type(self, data_type: str) -> bool:
        """Check if data type exists in mapping."""
        return data_type in self._accessors
    
    def data_types(self) -> list:
        """Get list of all data types."""
        return list(self._accessors.keys())
    
    def validate(self, data_type: str, required_columns: list) -> bool:
        """Validate that required columns exist for a data type."""
        if data_type not in self._accessors:
            raise ValueError(f"Data type '{data_type}' not found in column mapping")
        
        accessor = self._accessors[data_type]
        missing = [col for col in required_columns if not accessor.has(col)]
        
        if missing:
            raise ValueError(
                f"Missing required columns in {data_type} mapping: {missing}\n"
                f"Please add these columns to config.COLUMN_MAPPING['{data_type}']"
            )
        
        return True
    
    def print_schema(self):
        """Print complete column schema for debugging."""
        print("=" * 80)
        print("COLUMN SCHEMA")
        print("=" * 80)
        
        for data_type in sorted(self._accessors.keys()):
            accessor = self._accessors[data_type]
            mapping = accessor.all()
            
            print(f"\n{data_type.upper()}:")
            print("-" * 80)
            
            for std_name in sorted(mapping.keys()):
                actual_name = mapping[std_name]
                print(f"  {std_name:30} → {actual_name}")
        
        print("\n" + "=" * 80)


# Global singleton instance
# Import this in other modules: from column_schema import col
col = ColumnSchema()


# Helper functions for backward compatibility
def get_column(data_type: str, column_name: str) -> str:
    """Get actual column name for a standard column name."""
    accessor = col.get_accessor(data_type)
    if accessor is None:
        raise ValueError(f"Data type '{data_type}' not found")
    
    return getattr(accessor, column_name)


def validate_columns(data_type: str, required_columns: list) -> bool:
    """Validate that required columns exist."""
    return col.validate(data_type, required_columns)


if __name__ == '__main__':
    # Test the column schema system
    print("Testing Column Schema System")
    print("=" * 80)
    
    # Print complete schema
    col.print_schema()
    
    # Test accessing columns
    print("\nTest accessing columns:")
    print(f"col.orders.order_id = '{col.orders.order_id}'")
    print(f"col.trades.trade_time = '{col.trades.trade_time}'")
    
    # Test validation
    print("\nTest validation:")
    try:
        col.validate('orders', ['order_id', 'timestamp', 'quantity'])
        print("✓ Validation passed")
    except ValueError as e:
        print(f"✗ Validation failed: {e}")
    
    print("\n" + "=" * 80)
    print("Column Schema System OK")
