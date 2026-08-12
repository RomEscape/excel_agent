"""Allow running as `python -m office_claw_sidecar`.

import 는 반드시 **절대 경로**여야 한다. PyInstaller 가 이 파일을 엔트리 스크립트로
받으면 패키지 컨텍스트 없이 최상위(`__main__`)로 실행하기 때문에, 상대 import
(`from .main import main`)는 다음으로 죽는다:

    ImportError: attempted relative import with no known parent package

번들된 앱에서만 재현되므로(개발 중에는 `python -m` 으로 돌아 패키지 컨텍스트가
있다) 릴리스 산출물을 실제로 실행해보기 전에는 드러나지 않는다.
"""

from office_claw_sidecar.main import main

main()
