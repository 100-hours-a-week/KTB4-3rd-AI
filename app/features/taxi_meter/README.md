# 택시 미터기

`POST /taxi_meter`는 미터기 사진에서 현재 요금을 읽어 `cost`로 돌려준다.

## 파일

`router.py`는 `POST /taxi_meter`를 받는다. 요청 본문을 검사한 뒤 `main.run`을 부른다.

`main.py`의 `run`은 요청 하나의 순서를 정한다. 이미지를 한 번 받는다. `IMAGE_DEVICE`가 `off`이면 `model_api_run`만 부른다. 그 외에는 `model_pipe_run`을 먼저 돌리고, 결과가 없거나 로컬 모델이 실패하면 `model_api_run`을 부른다. 이 서버에서 모델을 돌릴 때는 로컬 모델 구간을 한 요청씩 처리한다.

`pipeline.py`의 `model_pipe_run`은 OCR로 글자를 읽고 openjev로 요금 후보를 고른다. pass일 때만 요금을 돌려주고, 아니면 요금을 만들지 않는다. `model_api_run`은 luna API로 같은 사진을 읽어 요금을 만든다.

`image.py`는 사진 URL을 받아 온다. 파일이 비었거나 5MB를 넘거나 JPEG, PNG가 아니면 오류를 낸다.

`models.py`는 요청 필드 `image_URL`, `ride_channel_id`와 응답 필드 `success`, `cost`를 정의한다.
