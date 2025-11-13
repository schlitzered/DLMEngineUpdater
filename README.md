# DLM Engine Updater
## Overview
The DLM (Distributed Lock Manager) Engine Updater is a sophisticated Linux system update orchestration tool that coordinates the patching process across multiple systems using distributed locking. It ensures that only one system in a group updates at a time, preventing service disruptions and maintaining system availability during maintenance windows.
## Key Features
- **Distributed Locking**: Prevents multiple systems from updating simultaneously
- **State Management**: Maintains update state across reboots and failures
- **Flexible Script Execution**: Supports custom scripts at each phase of the update process
- **User-Specific Scripts**: Allows individual users to run custom pre/post update scripts in $HOME/dlm_engine_updater/(pre|post)_update.d/
- **Date Constraints**: Allows updates only on specific days (e.g., 3rd Friday of the month)
- **Comprehensive Logging**: Detailed logging with rotation and configurable levels
- **Failure Handling**: Maintains locks on failure to prevent cascading issues
- **Notification System**: External notification hooks for monitoring integration

## Installation
``` bash
pip install dlmengineupdater
```
## Configuration
Create the configuration file at : `/etc/dlm_engine_updater/.env`
``` dotenv
# this only bypasses the API call and runs the update process without locking
main_api_noop=false

# Required API configuration (sample values for noop mode)
main_api_lockname=sample-lock
main_api_endpoint=https://api.example.com/dlm
main_api_secretid=sample-secret-id
main_api_secret=sample-secret-key

# Optional API configuration
main_api_ca=/path/to/ca-certificate.pem

# Logging configuration
main_log_level=DEBUG
main_log_retention=7
main_log_file=/var/log/dlm_engine_updater/dlm_engine_updater.log

# Main configuration
main_basedir=/etc/dlm_engine_updater
main_wait=false
main_waitmax=3600

main_userscriptusers=["user1", "user2", "user3"]

# Plugin configuration
plugin_dummy_enabled=true
plugin_dummy_config_key1=value1
plugin_dummy_config_key2=value2
```
## Patching Workflow
The DLM Engine Updater follows a comprehensive 8-step workflow:
### 1. Phase **needs_update**
- Executes scripts in directory `needs_update.d/`
- Checks if system updates are available
- If any script returns non-zero, updates are needed
- If all scripts return zero or no scripts exist, process exits

**Example script** (`needs_update.d/01-check-packages`):
``` bash
#!/bin/bash
# Check for available package updates
dnf check-update --quiet
exit $?
```
### 2. Phase **lock_get**
- Attempts to acquire the distributed lock from the DLM service
- If `wait=true`, retries with random backoff until seconds `wait_max`
- If lock acquisition fails, process exits
- On success, proceeds to pre-update phase

### 3. Phase **pre_update**
- Executes scripts in directory `pre_update.d/`
- Performs preparatory tasks (graceful service shutdown, backups, etc.)
- Any script failure stops the process and maintains the lock

**Example script** (`pre_update.d/01-stop-services`):
``` bash
#!/bin/bash
# Gracefully stop application services
systemctl stop nginx
systemctl stop application-server
```
### 4. Phase **update**
- Executes scripts in directory `update.d/`
- Performs actual system updates
- Any script failure stops the process and maintains the lock

**Example script** (`update.d/01-install-updates`):
``` bash
#!/bin/bash
# Install system updates
dnf update -y
```
### 5. Phase **needs_reboot**
- Executes scripts in directory `needs_reboot.d/`
- Determines if system reboot is required
- If any script returns non-zero, system will reboot
- If no scripts exist, reboot is always performed

**Example script** (`needs_reboot.d/01-check-kernel`):
``` bash
#!/bin/bash
# Check if kernel was updated
needs-restarting -r
```
### 6. Phase (if needed) **reboot**
- Executes the configured `reboot_cmd`
- Sets state to before rebooting `post_update`
- System must be configured to run the updater on boot with flag `--after_reboot`

### 7. Phase **post_update**
- Executes scripts in directory `post_update.d/`
- Performs post-update validation and service restoration
- Any script failure stops the process and maintains the lock

**Example script** (`post_update.d/01-start-services`):
``` bash
#!/bin/bash
# Start services and verify they're healthy
systemctl start nginx
systemctl start application-server
sleep 10
curl -f http://localhost/health || exit 1
```
### 8. Phase **lock_release**
- Releases the distributed lock
- Cleans up state file
- Allows other systems to begin their update process

## Usage Examples
### Basic Usage
``` bash
# Run with default configuration
dlm-engine-updater

# Specify custom configuration
dlm-engine-updater --cfg /path/to/config.ini

# Run after system reboot
dlm-engine-updater --after_reboot
```
### Date Constraints
``` bash
# Only run on 2nd Tuesday of the month
dlm-engine-updater --date_constraint "2:Tue"

# Multiple constraints (2nd Tuesday OR 4th Friday)
dlm-engine-updater --date_constraint "2:Tue,4:Fri"
```
### Random Delay
``` bash
# Add random sleep (0-300 seconds) before starting
dlm-engine-updater --random_sleep 300
```
## System Integration
### Cron Example
``` cron
# Run every Tuesday at 2 AM with date constraint
0 2 * * * /usr/local/bin/dlm-engine-updater --date_constraint "2:Tue" --random_sleep 1800
```
### Systemd Timer Example
``` ini
# /etc/systemd/system/dlm-updater.timer
[Unit]
Description=DLM Engine Updater Timer

[Timer]
OnCalendar=*-*-* 02:00:00
RandomizedDelaySec=30m

[Install]
WantedBy=timers.target
```
### Boot Integration
``` ini
# /etc/systemd/system/dlm-updater-boot.service
[Unit]
Description=DLM Engine Updater Post-Boot
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/dlm-engine-updater --after_reboot

[Install]
WantedBy=multi-user.target
```
## Script Security Requirements
All scripts must meet these security criteria:
- Owned by root (UID 0)
- Executable by owner
- Not writable by group or others
- Located in configured script directories

## Monitoring and Notifications
### External Notifications
Scripts in `ext_notify.d/` receive these parameters:
1. Lock name
2. Lock acquisition status (True/False)
3. Updater running status (True/False)
4. Current phase
5. Current script name
6. Return code

### Failure Handling
Scripts in `on_failure.d/` are executed when any phase fails, receiving the same parameters as notification scripts.
## State Management
The updater maintains its state in a file, allowing it to resume after interruptions or reboots. States include:
- : Initial state `needs_update`
- : Need to acquire distributed lock `lock_get`
- : Execute pre-update scripts `pre_update`
- : Execute update scripts `update`
- : Check if reboot required `needs_reboot`
- : System reboot needed `reboot`
- : Execute post-update scripts `post_update`
- : Release lock and cleanup `lock_release`

## Best Practices
1. **Test Scripts Individually**: Ensure each script works independently
2. **Implement Timeouts**: Add timeouts to prevent hanging operations
3. **Use Idempotent Operations**: Scripts should handle multiple executions safely
4. **Monitor Lock Status**: Set up alerts for systems holding locks too long
5. **Backup Before Updates**: Include backup operations in pre-update scripts
6. **Validate After Updates**: Implement comprehensive health checks in post-update scripts

## Troubleshooting
### Common Issues
1. **Lock Acquisition Timeout**: Check DLM service connectivity and other systems' status
2. **Script Permissions**: Verify scripts meet security requirements
3. **State File Corruption**: Delete state file to reset (will restart from beginning)
4. **Network Connectivity**: Ensure systems can reach the DLM service
5. **Certificate Issues**: Verify CA certificates and API credentials

### Debugging
``` bash
# Enable debug logging
dlm-engine-updater --cfg /path/to/debug-config.ini

# Check current state
cat /var/lib/dlm_engine_updater/state

# View recent logs
tail -f /var/log/dlm_engine_updater.log
```
## API Compatibility
The updater supports both DLM API v1 and v2, automatically detecting the version based on the endpoint URL format.
## Contributing
Contributions are welcome! Please ensure:
- Code follows Python best practices
- Add tests for new functionality
- Update documentation as needed
- Follow the existing error handling patterns
