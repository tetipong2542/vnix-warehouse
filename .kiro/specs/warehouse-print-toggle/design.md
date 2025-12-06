# Design Document

## Overview

This design document outlines the implementation of a print toggle feature for the Warehouse Job Sheet report. The feature allows admin users to enable or disable printing functionality, providing better control over when warehouse staff can print job sheets. The system will persist the print status in the database and display it clearly to all users.

## Architecture

The solution follows a client-server architecture with the following components:

### Backend (Flask)
- New database model to store print toggle status
- API endpoint to get current print status
- API endpoint to update print status (admin only)
- Middleware to check print status before allowing print operations

### Frontend (JavaScript + HTML)
- Toggle UI component (visible to admin only)
- Status display component (visible to all users)
- Real-time status updates via AJAX
- Button state management based on print status

### Database
- New table `print_settings` to store configuration
- Columns: id, setting_key, setting_value, updated_by, updated_at

## Components and Interfaces

### 1. Database Model

**PrintSetting Model** (models.py)
```python
class PrintSetting(db.Model):
    __tablename__ = "print_settings"
    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    setting_value = db.Column(db.String(255), nullable=False)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(TH_TZ))
    
    updated_by = db.relationship("User", foreign_keys=[updated_by_user_id])
```

### 2. Backend API Endpoints

**GET /api/print_status/warehouse**
- Returns current print status for warehouse reports
- Response: `{"enabled": true/false, "updated_by": "username", "updated_at": "ISO timestamp"}`
- Authentication: Required (any logged-in user)

**POST /api/print_status/warehouse**
- Updates print status for warehouse reports
- Request body: `{"enabled": true/false}`
- Response: `{"success": true, "enabled": true/false, "message": "..."}`
- Authentication: Required (admin only)
- Authorization: Returns 403 if user is not admin

### 3. Frontend Components

**Print Toggle Switch** (for admin users)
- Bootstrap toggle switch or custom styled checkbox
- Located near print buttons in the toolbar
- Shows current state (ON/OFF)
- Triggers AJAX call on change
- Displays loading state during update
- Shows success/error feedback

**Status Banner** (for all users)
- Alert box displaying current print status
- Green (success) when printing is enabled
- Red (danger) when printing is disabled
- Shows last updated by and timestamp
- Auto-updates when status changes

**Button State Manager**
- JavaScript function to enable/disable print buttons
- Applies visual styling (opacity, cursor)
- Prevents click events when disabled
- Shows tooltip on hover explaining why disabled

## Data Models

### PrintSetting Table Schema

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| setting_key | VARCHAR(64) | UNIQUE, NOT NULL, INDEX | Setting identifier (e.g., 'warehouse_print_enabled') |
| setting_value | VARCHAR(255) | NOT NULL | Setting value (stored as string, e.g., 'true'/'false') |
| updated_by_user_id | INTEGER | FOREIGN KEY(users.id) | User who last updated this setting |
| updated_at | DATETIME | NOT NULL | Timestamp of last update |

### Default Values

On system initialization, the following default setting will be created:
- `setting_key`: 'warehouse_print_enabled'
- `setting_value`: 'true'
- `updated_by_user_id`: NULL (system default)
- `updated_at`: Current timestamp

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Admin-only toggle access
*For any* user attempting to change the print status, the system should allow the operation if and only if the user has admin role
**Validates: Requirements 1.5**

### Property 2: Status persistence
*For any* print status change, querying the status immediately after the change should return the new status value
**Validates: Requirements 5.1, 5.2**

### Property 3: Button state consistency
*For any* print status value, all print buttons should be enabled when status is true and disabled when status is false
**Validates: Requirements 3.1, 3.2, 3.3**

### Property 4: Status visibility
*For any* user viewing the warehouse report page, the current print status should be displayed regardless of user role
**Validates: Requirements 2.1, 2.2, 2.3**

### Property 5: Audit trail completeness
*For any* print status change, the system should record both the username and timestamp of the change
**Validates: Requirements 6.1, 6.2**

### Property 6: Multi-user consistency
*For any* two users viewing the page simultaneously, both should see the same print status after any status change
**Validates: Requirements 5.4**

## Error Handling

### Backend Error Scenarios

1. **Database Connection Failure**
   - Return 500 error with message "Database connection failed"
   - Log error details for debugging
   - Frontend displays generic error message

2. **Unauthorized Access (Non-admin trying to toggle)**
   - Return 403 Forbidden
   - Message: "Only administrators can change print settings"
   - Frontend displays error alert

3. **Invalid Request Data**
   - Return 400 Bad Request
   - Message: "Invalid request format"
   - Frontend displays validation error

4. **Setting Not Found**
   - Create default setting automatically
   - Return success with default value
   - Log warning for monitoring

### Frontend Error Scenarios

1. **Network Failure**
   - Display error message: "Connection failed. Please check your network."
   - Retry button available
   - Toggle reverts to previous state

2. **Timeout**
   - Display message: "Request timed out. Please try again."
   - Auto-retry after 3 seconds (max 2 retries)

3. **Unexpected Response**
   - Display generic error message
   - Log error to console for debugging
   - Maintain current UI state

## Testing Strategy

### Unit Tests

1. **Database Model Tests**
   - Test PrintSetting model creation
   - Test unique constraint on setting_key
   - Test relationship with User model
   - Test default values

2. **API Endpoint Tests**
   - Test GET /api/print_status/warehouse returns correct format
   - Test POST /api/print_status/warehouse with admin user succeeds
   - Test POST /api/print_status/warehouse with non-admin user returns 403
   - Test POST /api/print_status/warehouse with invalid data returns 400
   - Test POST /api/print_status/warehouse updates database correctly

3. **Authorization Tests**
   - Test admin user can access toggle endpoint
   - Test regular user cannot access toggle endpoint
   - Test unauthenticated user is redirected to login

### Property-Based Tests

Property-based tests will be implemented using Hypothesis (Python's property-based testing library) to verify the correctness properties defined above.

1. **Property Test: Admin-only Access**
   - Generate random user objects with various roles
   - Verify only admin users can successfully toggle print status
   - **Feature: warehouse-print-toggle, Property 1: Admin-only toggle access**

2. **Property Test: Status Persistence**
   - Generate random boolean values for print status
   - Set status and immediately query it
   - Verify returned value matches set value
   - **Feature: warehouse-print-toggle, Property 2: Status persistence**

3. **Property Test: Button State Consistency**
   - Generate random print status values
   - Verify all print buttons have consistent enabled/disabled state
   - **Feature: warehouse-print-toggle, Property 3: Button state consistency**

4. **Property Test: Audit Trail**
   - Generate random status changes with different users
   - Verify each change records username and timestamp
   - **Feature: warehouse-print-toggle, Property 5: Audit trail completeness**

### Integration Tests

1. **End-to-End Toggle Flow**
   - Admin logs in
   - Navigates to warehouse report
   - Toggles print status
   - Verifies UI updates
   - Verifies buttons are enabled/disabled
   - Refreshes page and verifies status persists

2. **Multi-User Scenario**
   - Two users view warehouse report simultaneously
   - Admin toggles print status
   - Verify both users see updated status (may require page refresh or WebSocket)

3. **Print Operation with Status Check**
   - Set print status to disabled
   - Attempt to print
   - Verify print operation is blocked
   - Set print status to enabled
   - Verify print operation succeeds

## Implementation Notes

### Security Considerations

1. **Authorization**: Always verify user role on server-side before allowing status changes
2. **Input Validation**: Validate all input data to prevent injection attacks
3. **CSRF Protection**: Use Flask's CSRF protection for POST requests
4. **Audit Logging**: Log all status changes for security auditing

### Performance Considerations

1. **Caching**: Consider caching print status in memory to reduce database queries
2. **Database Indexing**: Index on setting_key for fast lookups
3. **Lazy Loading**: Load print status only when warehouse report page is accessed

### UI/UX Considerations

1. **Visual Feedback**: Provide immediate visual feedback when toggle is clicked
2. **Clear Messaging**: Use clear, concise messages to explain print status
3. **Accessibility**: Ensure toggle is keyboard accessible and screen-reader friendly
4. **Mobile Responsive**: Ensure toggle works well on mobile devices

### Migration Strategy

1. Create new PrintSetting table via migration
2. Insert default setting (warehouse_print_enabled = true)
3. No changes to existing OrderLine table required
4. Backward compatible - if setting not found, default to enabled
