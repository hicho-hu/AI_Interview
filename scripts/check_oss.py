"""Opt-in OSS connectivity probe; never prints keys, tokens or signed URLs."""

import argparse
import hashlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.cache import Cache  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.errors import BusinessError  # noqa: E402
from app.core.security import Security  # noqa: E402
from app.infrastructure.storage.oss_storage import OSSStorage  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-probe", action="store_true", help="Upload only generated probe text and clean it up"
    )
    args = parser.parse_args()
    keys = []
    cloud = None
    try:
        settings = Settings(storage_backend="oss")
        cloud = OSSStorage(settings, Security(settings), Cache(settings))
        cloud.sts.server()
        print("STS: OK", flush=True)
        cloud.check_ready()
        print("Private bucket: OK", flush=True)
        if args.write_probe:
            uid = "connectivity-check"
            key = f"ai-interview/user/{uid}/incoming/{uuid.uuid4().hex}.txt"
            keys.extend([key, key.replace("/incoming/", "/accepted/", 1)])
            content = ("ai-interview synthetic connectivity probe " + uuid.uuid4().hex).encode()
            credentials = cloud.sts.upload(uid, key)
            import oss2
            from oss2.credentials import StaticCredentialsProvider

            client = oss2.Bucket(
                oss2.ProviderAuthV4(
                    StaticCredentialsProvider(
                        credentials["access_key_id"],
                        credentials["access_key_secret"],
                        credentials["security_token"],
                    )
                ),
                settings.oss_endpoint,
                settings.oss_bucket_name,
                region=settings.oss_region,
                connect_timeout=10,
            )
            cloud._run(lambda: client.put_object(key, content, headers={"Content-Type": "text/plain"}))
            head = cloud.head(key)
            if head["size"] != len(content) or cloud.read_version(key, head["etag"]) != content:
                raise BusinessError("OSS_PROBE_MISMATCH", "OSS 测试内容不一致", 503)
            accepted = cloud.finalize(SimpleNamespace(object_key=key), head["etag"])
            if hashlib.sha256(cloud.read(accepted)).digest() != hashlib.sha256(content).digest():
                raise BusinessError("OSS_PROBE_MISMATCH", "OSS 测试内容不一致", 503)
            print("Scoped upload / read / conditional copy: OK", flush=True)
        return 0
    except BusinessError as exc:
        print(f"FAILED: {exc.code} — {exc.message}", flush=True)
        return 1
    except Exception as exc:
        # SDK exception text can contain an AccessKey ID or a signed request.
        print(f"FAILED: {type(exc).__name__}; check backend OSS configuration/network", flush=True)
        return 1
    finally:
        if cloud and keys:
            for key in keys:
                try:
                    cloud.delete(key)
                except Exception:
                    print("Probe cleanup failed; check the connectivity-check object prefix", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
