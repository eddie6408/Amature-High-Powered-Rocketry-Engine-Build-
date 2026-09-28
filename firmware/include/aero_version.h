/* Firmware identity. The build system injects these values; the flight
 * computer reports them to the ground station in the identity frame, and they
 * are stored in every flight record. */
#ifndef AERO_VERSION_H
#define AERO_VERSION_H

#ifndef AERO_FW_VERSION
#define AERO_FW_VERSION "FW-0.1.0"
#endif
#ifndef AERO_FW_COMMIT
#define AERO_FW_COMMIT "unknown"
#endif
#ifndef AERO_FW_BUILD_TIME
#define AERO_FW_BUILD_TIME "unknown"
#endif
#ifndef AERO_HW_VERSION
#define AERO_HW_VERSION "FC-HW-001"
#endif

const char *aero_fw_version(void);
const char *aero_fw_commit(void);
const char *aero_fw_build_time(void);

#endif
