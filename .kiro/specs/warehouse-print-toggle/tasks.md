# Implementation Plan

- [x] 1. Create database model and migration
  - Create PrintSetting model in models.py with all required fields
  - Add database migration function to create print_settings table
  - Insert default setting (warehouse_print_enabled = true) on initialization
  - _Requirements: 5.1, 5.2_

- [ ]* 1.1 Write property test for status persistence
  - **Property 2: Status persistence**
  - **Validates: Requirements 5.1, 5.2**

- [x] 2. Implement backend API endpoints
  - [x] 2.1 Create GET /api/print_status/warehouse endpoint
    - Return current print status with metadata (updated_by, updated_at)
    - Require authentication (any logged-in user)
    - Handle case when setting doesn't exist (return default)
    - _Requirements: 2.1, 5.3_

  - [x] 2.2 Create POST /api/print_status/warehouse endpoint
    - Accept enabled boolean in request body
    - Verify user is admin (return 403 if not)
    - Update print status in database
    - Record username and timestamp
    - Return success response with new status
    - _Requirements: 1.2, 1.3, 6.1, 6.2_

  - [ ]* 2.3 Write property test for admin-only access
    - **Property 1: Admin-only toggle access**
    - **Validates: Requirements 1.5**

  - [ ]* 2.4 Write unit tests for API endpoints
    - Test GET endpoint returns correct format
    - Test POST endpoint with admin user succeeds
    - Test POST endpoint with non-admin returns 403
    - Test POST endpoint with invalid data returns 400
    - Test POST endpoint updates database correctly
    - _Requirements: 1.2, 1.3, 1.5_

- [x] 3. Update warehouse report route to include print status
  - Modify print_warehouse() function to fetch current print status
  - Pass print status to template context
  - Pass user role to template for conditional rendering
  - _Requirements: 1.1, 2.1_

- [x] 4. Implement frontend toggle UI component
  - [x] 4.1 Add toggle switch HTML in report.html (admin only)
    - Use Bootstrap toggle or custom styled checkbox
    - Position near print buttons in toolbar
    - Show current state (ON/OFF)
    - Add tooltip explaining function
    - Conditionally render based on user role
    - _Requirements: 1.1, 4.1, 4.2_

  - [x] 4.2 Implement JavaScript toggle handler
    - Listen for toggle change events
    - Show loading state during update
    - Make AJAX POST request to update status
    - Handle success response (update UI)
    - Handle error response (show error, revert toggle)
    - Provide visual feedback (success/error message)
    - _Requirements: 1.2, 1.3, 1.4, 4.3, 4.4_

- [x] 5. Implement status display banner
  - [x] 5.1 Add status banner HTML in report.html
    - Create alert box for status display
    - Show green (success) when enabled
    - Show red (danger) when disabled
    - Display last updated by and timestamp
    - Position prominently at top of page
    - _Requirements: 2.1, 2.2, 2.3, 6.3_

  - [x] 5.2 Implement status update function
    - JavaScript function to update banner when status changes
    - Update color and message based on status
    - Update metadata (username, timestamp)
    - _Requirements: 1.4, 2.4_

- [x] 6. Implement button state management
  - [x] 6.1 Create JavaScript function to control button states
    - Enable/disable all print buttons based on status
    - Apply visual styling (opacity, cursor) to disabled buttons
    - Add/remove disabled attribute
    - _Requirements: 3.1, 3.2_

  - [x] 6.2 Add click prevention for disabled buttons
    - Prevent click events when buttons are disabled
    - Show tooltip explaining why disabled
    - Display informative message on click attempt
    - _Requirements: 3.3_

  - [ ]* 6.3 Write property test for button state consistency
    - **Property 3: Button state consistency**
    - **Validates: Requirements 3.1, 3.2, 3.3**

- [x] 7. Update print commit endpoint to check status
  - Modify print_warehouse_commit() to check print status before processing
  - Return error if printing is disabled
  - Display error message to user
  - _Requirements: 3.3_

- [ ] 8. Add error handling
  - [ ] 8.1 Backend error handling
    - Handle database connection failures
    - Handle unauthorized access (403)
    - Handle invalid request data (400)
    - Handle missing settings (create default)
    - Add appropriate error logging
    - _Requirements: All_

  - [ ] 8.2 Frontend error handling
    - Handle network failures (show error, retry button)
    - Handle timeouts (auto-retry with limit)
    - Handle unexpected responses (generic error)
    - Maintain UI state on errors
    - _Requirements: All_

- [ ]* 9. Write property test for audit trail
  - **Property 5: Audit trail completeness**
  - **Validates: Requirements 6.1, 6.2**

- [ ]* 10. Write integration tests
  - Test end-to-end toggle flow (admin login, toggle, verify)
  - Test print operation with status check (disabled blocks, enabled allows)
  - Test status display for different user roles
  - _Requirements: All_

- [x] 11. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Add CSS styling for toggle and status components
  - Style toggle switch for better UX
  - Style status banner for visibility
  - Style disabled buttons clearly
  - Ensure mobile responsiveness
  - Add hover effects and transitions
  - _Requirements: 4.1, 4.2_

- [ ] 13. Update documentation
  - Add comments to new code
  - Document API endpoints
  - Add user guide for print toggle feature
  - _Requirements: All_

- [x] 14. Final Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
