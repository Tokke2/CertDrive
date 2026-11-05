# Compatibility shim for installations that still have the old scheduled task.
# It never updates Python or packages; it removes the obsolete task and exits.
$TaskName = 'IntygsDrive Python Weekly Update'
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
} catch {
    # The app also removes this task on startup; no update work is performed here.
}
exit 0
