@echo off
setlocal EnableExtensions

set "PACKAGE=%TEMP%\infomancer-testing-mie.tar.gz"
set "ATLAS=atlas@192.168.0.158"
set "REMOTE_PACKAGE=/home/atlas/infomancer-testing-mie.tar.gz"

pushd "%~dp0" || goto :local_error

echo [1/4] Packaging the current InfoMancer workspace...
if exist "%PACKAGE%" del /q "%PACKAGE%"
tar ^
  --exclude=.git ^
  --exclude=graft ^
  --exclude=.venv ^
  --exclude=**pycache** ^
  --exclude=data ^
  --exclude=data-sandbox ^
  --exclude=sandbox-media ^
  --exclude=backups ^
  --exclude=dist ^
  --exclude=.env ^
  --exclude=.env.cloudflare ^
  --exclude=.env.sandbox ^
  -czf "%PACKAGE%" .
if errorlevel 1 goto :package_error

echo [2/4] Copying the package to Atlas...
scp "%PACKAGE%" "%ATLAS%:%REMOTE_PACKAGE%"
if errorlevel 1 goto :copy_error

echo [3/4] Backing up and rebuilding the main InfoMancer instance...
ssh "%ATLAS%" "set -e; cd /home/atlas/infomancer; mkdir -p backups; if [ -f data/infomancer.db ]; then cp data/infomancer.db backups/main-before-mie-$(date +%%Y%%m%%d-%%H%%M%%S).db; fi; tar -xzf /home/atlas/infomancer-testing-mie.tar.gz -C /home/atlas/infomancer; docker compose -p infomancer -f compose.yaml -f compose.atlas.yaml -f compose.cloudflare.yaml up -d --build --remove-orphans; echo Waiting for InfoMancer to finish starting...; for attempt in $(seq 1 30); do if curl -fsS http://127.0.0.1:8787/health 2>/dev/null; then echo; echo InfoMancer is healthy.; docker compose -p infomancer -f compose.yaml -f compose.atlas.yaml -f compose.cloudflare.yaml ps; exit 0; fi; sleep 2; done; echo InfoMancer did not become healthy within 60 seconds.; echo Recent application logs:; docker compose -p infomancer -f compose.yaml -f compose.atlas.yaml -f compose.cloudflare.yaml logs --tail=100 infomancer; exit 1"
if errorlevel 1 goto :remote_error

echo [4/4] Deployment completed successfully.
del /q "%PACKAGE%" 2>nul
popd
exit /b 0

:package_error
echo ERROR: InfoMancer could not be packaged. Review the tar error above.
goto :failed

:copy_error
echo ERROR: The package could not be copied to Atlas. Check the network connection and SSH credentials.
goto :failed

:remote_error
echo ERROR: Atlas received the package, but the deployment did not finish successfully.
echo If the app failed to start, the recent application logs above explain what went wrong.
goto :failed

:local_error
echo ERROR: The InfoMancer project folder could not be opened.
exit /b 1

:failed
popd
exit /b 1
