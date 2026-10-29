"""Real Linux/Docker checks. Run only on a disposable CI runner or test server."""
import json
from pathlib import Path
import subprocess

from deploy.broker import execute


def check(payload, expected, output=None):
    result = execute(payload)
    assert result.get("status") == expected, result
    if output is not None:
        assert result.get("stdout", "").strip() == output, result
    return result


def main():
    assert execute({"action": "info"}).get("ok"), "GCC unavailable"
    question = {"cases": [{"input": "2 5\n", "output": "7"}]}
    addition = '#include <stdio.h>\nint main(void){int a,b;scanf("%d%d",&a,&b);printf("%d",a+b);return 0;}'
    check({"action": "run", "code": addition, "stdin": "3 4\n"}, "OK", "7")
    check({"action": "judge", "question": question, "code": addition}, "AC")
    check({"action": "judge", "question": question, "code": "int main(void){return 0;}"}, "WA")
    check({"action": "run", "code": "this is not C"}, "CE")
    check({"action": "run", "code": '#include <stdlib.h>\nint main(void){abort();}'}, "RE")
    check({"action": "run", "code": "int main(void){for(;;){}}"}, "TLE")
    check({"action": "run", "code": '#include <stdio.h>\nint main(void){for(;;)puts("output");}'}, "RE")
    isolation = r'''
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/socket.h>
#include <arpa/inet.h>
int main(void) {
    const char *paths[] = {"/var/lib/cmaster/users.json", "/run/cmaster/judge.sock",
                          "/var/run/docker.sock", "/app/data/answers.json"};
    for (int i=0; i<4; i++) if (access(paths[i], F_OK) == 0) return 10+i;
    if (getuid() == 0 || getenv("CMASTER_SECRET")) return 20;
    FILE *file = fopen("/app/must-not-write", "w");
    if (file) { fclose(file); return 21; }
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in remote = {.sin_family=AF_INET, .sin_port=htons(443)};
    inet_pton(AF_INET, "1.1.1.1", &remote.sin_addr);
    if (fd >= 0 && connect(fd, (struct sockaddr *)&remote, sizeof(remote)) == 0) return 22;
    if (fd >= 0) close(fd);
    puts("isolated");
    return 0;
}'''
    check({"action": "run", "code": isolation}, "OK", "isolated")
    check({"action": "run", "code": '#include "/var/lib/cmaster/users.json"\nint main(void){return 0;}'}, "CE")
    root = Path(__file__).resolve().parents[1]
    questions = json.loads((root / "data/question_bank.json").read_text(encoding="utf-8"))
    answers = json.loads((root / "data/answers.json").read_text(encoding="utf-8"))
    for question in questions:
        check({"action": "judge", "question": question, "code": answers[question["id"]]}, "AC")
    remaining = subprocess.check_output(["docker", "ps", "-aq", "--filter", "name=cmaster-job-"], text=True)
    assert not remaining.strip(), "Judge containers left behind"
    print(f"Docker smoke passed: real verdicts, isolation, cleanup, {len(questions)} reference answers.")


if __name__ == "__main__":
    main()
