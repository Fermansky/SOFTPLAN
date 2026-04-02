from ..services import MinioStorage, get_minio_storage as get_minio_storage_service


def get_minio_storage() -> MinioStorage:
    return get_minio_storage_service()
