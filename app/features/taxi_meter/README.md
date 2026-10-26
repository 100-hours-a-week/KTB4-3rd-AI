# 택시 미터기

`POST /taxi_meter`는 미터기 사진에서 현재 요금을 읽어 `cost`로 돌려준다.

## 파일

`router.py`는 `POST /taxi_meter`를 받는다(`async def`). 요청 본문을 검사한 뒤 `app.state.scheduler.submit("taxi_meter", image_URL)`로 내부 큐(`app/inner_queue`)에 넣고 결과를 기다린다. 큐가 API 경로면 `main.run_api`, GPU 경로면 `main.run_gpu`를 부른다.

`main.py`는 경로별 실행 함수 두 개를 둔다. 둘 다 `async def`이고 이미지를 한 번 받는다. `run_api`는 바로 `model_api_run`(luna)을 부른다. `run_gpu`는 이미지 모델을 쓰지 않는 환경(`LOCAL_DEVICE=FALSE`이고 Modal `_1` 키 없음)이면 `model_api_run`만 부른다. Modal이면 `llm.pipe`로 원격 `model_pipe_run`을 한 번 부르고, 로컬이면 `model_pipe_run`을 직접 부른다. 결과가 없거나, Modal이 준비 중이거나, 모델이 실패하면 `model_api_run`을 부른다. GPU 경로는 큐에서 한 번에 한 건씩 돈다.

`pipeline.py`의 두 함수는 `async def`이고 안에서 `await llm(...)`을 부른다. `model_pipe_run`은 OCR로 글자를 읽고 openjev로 요금 후보를 고른다. pass일 때만 요금을 돌려주고, 아니면 요금을 만들지 않는다. `model_api_run`은 luna API로 같은 사진을 읽어 요금을 만든다.

`image.py`의 `load_image`는 `async def`로 사진 URL을 받아 온다. 리다이렉트는 따라가지 않고, 다운로드 전체를 5초로 끊는다. 파일이 비었거나 5MB를 넘거나 JPEG, PNG가 아니면 오류를 낸다.

`models.py`는 요청 필드 `image_URL`과 응답 필드 `success`, `cost`를 정의한다.
