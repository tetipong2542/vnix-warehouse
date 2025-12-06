# Requirements Document

## Introduction

ระบบใบงานคลัง (Warehouse Job Sheet) ปัจจุบันมีฟังก์ชันการพิมพ์และล็อกงาน แต่ยังไม่มีระบบควบคุมการเปิด/ปิดการพิมพ์ ทำให้ไม่สามารถป้องกันการพิมพ์ในช่วงเวลาที่ไม่เหมาะสม หรือควบคุมสิทธิ์การพิมพ์ได้

ฟีเจอร์นี้จะเพิ่มระบบควบคุมการเปิด/ปิดการพิมพ์ โดยมีสถานะที่สามารถสลับได้ และแสดงสถานะปัจจุบันให้ผู้ใช้เห็นอย่างชัดเจน

## Glossary

- **Warehouse Report**: หน้ารายงานใบงานคลังที่แสดงรายการออเดอร์ที่พร้อมจัดเตรียม
- **Print Toggle**: สวิตช์หรือปุ่มสำหรับเปิด/ปิดการพิมพ์
- **Print Status**: สถานะปัจจุบันของระบบการพิมพ์ (เปิดใช้งาน/ปิดใช้งาน)
- **Admin User**: ผู้ใช้ที่มี role เป็น 'admin' ในระบบ
- **Regular User**: ผู้ใช้ทั่วไปที่ไม่ใช่ admin
- **Print Lock**: การล็อกงานหลังจากพิมพ์เพื่อป้องกันการพิมพ์ซ้ำ

## Requirements

### Requirement 1

**User Story:** As an admin user, I want to toggle the print functionality on/off, so that I can control when warehouse staff can print job sheets.

#### Acceptance Criteria

1. WHEN an admin user views the warehouse report page THEN the system SHALL display a print toggle control
2. WHEN an admin user clicks the print toggle control THEN the system SHALL change the print status between enabled and disabled
3. WHEN the print status changes THEN the system SHALL persist the new status to the database
4. WHEN the print status changes THEN the system SHALL update the UI to reflect the current status without requiring a page reload
5. WHERE the user is not an admin THEN the system SHALL hide the print toggle control

### Requirement 2

**User Story:** As a warehouse staff member, I want to see the current print status clearly, so that I know whether I can print job sheets.

#### Acceptance Criteria

1. WHEN any user views the warehouse report page THEN the system SHALL display the current print status prominently
2. WHILE printing is disabled THEN the system SHALL display a warning message indicating that printing is currently disabled
3. WHILE printing is enabled THEN the system SHALL display a success message indicating that printing is available
4. WHEN the print status changes THEN the system SHALL update the status display immediately

### Requirement 3

**User Story:** As a warehouse staff member, I want the print buttons to be disabled when printing is turned off, so that I cannot accidentally attempt to print when it's not allowed.

#### Acceptance Criteria

1. WHILE printing is disabled THEN the system SHALL disable all print-related buttons
2. WHILE printing is disabled THEN the system SHALL apply visual styling to indicate buttons are disabled
3. WHEN a user attempts to click a disabled print button THEN the system SHALL prevent the action and display an informative message
4. WHILE printing is enabled THEN the system SHALL enable all print-related buttons and allow normal printing operations

### Requirement 4

**User Story:** As an admin user, I want the print toggle to be easily accessible, so that I can quickly enable or disable printing when needed.

#### Acceptance Criteria

1. WHEN an admin user views the warehouse report page THEN the system SHALL position the print toggle control in a prominent location near the print buttons
2. WHEN an admin user hovers over the print toggle THEN the system SHALL display a tooltip explaining its function
3. WHEN the print toggle is clicked THEN the system SHALL provide immediate visual feedback
4. WHEN the toggle operation completes THEN the system SHALL display a confirmation message

### Requirement 5

**User Story:** As a system administrator, I want the print status to persist across sessions, so that the setting remains consistent until explicitly changed.

#### Acceptance Criteria

1. WHEN the system starts THEN the system SHALL load the last saved print status from the database
2. WHEN the print status is changed THEN the system SHALL save the new status to the database immediately
3. WHEN a user refreshes the page THEN the system SHALL display the current print status from the database
4. WHEN multiple users view the page simultaneously THEN the system SHALL display the same print status to all users

### Requirement 6

**User Story:** As a warehouse manager, I want to see who last changed the print status and when, so that I can track print control changes for accountability.

#### Acceptance Criteria

1. WHEN the print status is changed THEN the system SHALL record the username of the user who made the change
2. WHEN the print status is changed THEN the system SHALL record the timestamp of the change
3. WHEN an admin user views the warehouse report page THEN the system SHALL display the last change information including username and timestamp
4. WHEN no changes have been made THEN the system SHALL display a default message indicating the initial status
