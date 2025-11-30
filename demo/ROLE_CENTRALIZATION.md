# Role Permission Centralization Summary

## Problem
Role descriptions and permissions were scattered across multiple files, making it hard to maintain consistency.

## Solution
Centralized all role-related information in `prime_guardrails/iam/roles.py`.

## What's Centralized

### 1. Role Definitions
**Location:** `prime_guardrails/iam/roles.py`
```python
class UserRole(Enum):
    USER = "user"
    STAFF = "staff"
    ADMIN = "admin"
    SYSTEM = "system"
```

### 2. Role Permissions
**Location:** `prime_guardrails/iam/roles.py`
```python
ROLE_PERMISSIONS: dict[UserRole, Set[Permission]] = {
    UserRole.USER: {...},
    UserRole.STAFF: {...},
    UserRole.ADMIN: {...},
}
```

### 3. Role Descriptions (NEW)
**Location:** `prime_guardrails/iam/roles.py`
```python
def get_role_description(role: UserRole) -> str:
    """Get description for a single role"""

def get_all_role_descriptions() -> str:
    """Get formatted descriptions for all roles (for prompts)"""
```

## Files Updated

### `prime_guardrails/iam/roles.py`
- ✅ Added `get_role_description()` function
- ✅ Added `get_all_role_descriptions()` function

### `prime_guardrails/iam/__init__.py`
- ✅ Exported new functions

### `prime_guardrails/observability_tools.py`
- ✅ Imported `get_all_role_descriptions`
- ✅ Replaced hardcoded role descriptions in `safety_check_layer2`
- ✅ Replaced hardcoded role descriptions in `make_safe_and_compliant_decision`

## Benefits

1. **Single Source of Truth**: Role descriptions defined once in `iam/roles.py`
2. **Consistency**: All prompts use the same role descriptions
3. **Maintainability**: Change role capabilities in one place, updates everywhere
4. **Type Safety**: Functions return consistent formats

## Usage Example

```python
from prime_guardrails.iam import get_all_role_descriptions, get_role_description, UserRole

# Get all role descriptions for a prompt
prompt = f"""
User roles:
{get_all_role_descriptions()}
"""

# Get description for a specific role
staff_desc = get_role_description(UserRole.STAFF)
# Returns: "Bank employee, authorized to view customer data..."
```

## What's Still TODO (Optional Future Work)

1. **Tool Permissions**: Currently `get_tool_descriptions()` in `prompt.py` has hardcoded logic for which tools each role can see. Could be moved to `iam/roles.py` as well.

2. **Session Management**: Session IDs are now supported in audit logs but not automatically populated yet.

3. **Dynamic User Switching**: The ADK web UI doesn't support dynamic user switching - requires server restart.
