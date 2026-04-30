#!/usr/bin/env .venv/bin/python
import asyncio
import sys
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.mobile_config import MobileConfigService

async def check_enrollment():
    try:
        lockdown = await create_using_usbmux()
        async with MobileConfigService(lockdown) as svc:
            config = await svc.get_cloud_configuration()
            print("Cloud Configuration:")
            for key, value in config.items():
                print(f"  {key}: {value}")
            return True
    except Exception as e:
        print(f"Error checking enrollment: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    result = asyncio.run(check_enrollment())
    sys.exit(0 if result else 1)