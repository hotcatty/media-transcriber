/* Mach-O stub so Finder launches a real binary, then execs the bash helper. */
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
  char exe[PATH_MAX];
  uint32_t n = sizeof(exe);
  if (_NSGetExecutablePath(exe, &n) != 0) {
    return 127;
  }
  char resolved[PATH_MAX];
  if (realpath(exe, resolved) == NULL) {
    strncpy(resolved, exe, sizeof(resolved) - 1);
    resolved[sizeof(resolved) - 1] = '\0';
  }
  char *slash = strrchr(resolved, '/');
  if (slash == NULL) {
    return 127;
  }
  *slash = '\0';
  char script[PATH_MAX];
  if (snprintf(script, sizeof(script), "%s/launcher.bash", resolved) >= (int)sizeof(script)) {
    return 127;
  }

  char **args = calloc((size_t)argc + 2, sizeof(char *));
  if (args == NULL) {
    return 127;
  }
  args[0] = script;
  for (int i = 1; i < argc; i++) {
    args[i] = argv[i];
  }
  execv(script, args);

  char **bargs = calloc((size_t)argc + 3, sizeof(char *));
  if (bargs == NULL) {
    return 127;
  }
  bargs[0] = "bash";
  bargs[1] = script;
  for (int i = 1; i < argc; i++) {
    bargs[i + 1] = argv[i];
  }
  execv("/bin/bash", bargs);
  return 127;
}
