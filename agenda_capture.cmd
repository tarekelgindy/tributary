@echo off
rem Tributary agenda capture — run by Windows Task Scheduler every 6 hours
rem (task name: "Tributary agenda capture"; remove with:
rem    schtasks /delete /tn "Tributary agenda capture" /f )
rem Free: RSS + Wikipedia HTTP only, no API key, no spend. A missed run
rem (machine asleep) is just a gap in the sample — recorded by absence.
cd /d C:\Users\Tarek\Documents\tributary_tracer
wsl -e bash -c "cd /mnt/c/Users/Tarek/Documents/tributary_tracer && .venv/bin/python agenda.py --capture >> agenda/capture.log 2>&1 && .venv/bin/python agenda.py --universe >> agenda/capture.log 2>&1 && .venv/bin/python sitemaps.py --capture >> agenda/capture.log 2>&1"
