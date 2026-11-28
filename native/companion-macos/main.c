#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    if (argc != 3 || argv[1][0] != '/' || argv[2][0] != '/') {
        fputs("SwB Companion requires absolute Python and settings paths.\n", stderr);
        return 2;
    }
    struct stat config;
    if (lstat(argv[2], &config) != 0 || !S_ISDIR(config.st_mode) ||
        config.st_uid != getuid() || (config.st_mode & 0077) != 0) {
        fputs("Choose a private companion settings directory.\n", stderr);
        return 2;
    }
    char *const args[] = {
        argv[1], "-E", "-P", "-u", "-m", "switchboard.companion.service", argv[2], NULL
    };
    execv(argv[1], args);
    perror("SwB Companion could not start Python");
    return 1;
}
