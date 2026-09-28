/* 2-state altitude/velocity Kalman filter. Mirrors
 * src/aerodyne/avionics/estimation.py::AltitudeKalmanFilter. */
#ifndef AERO_KALMAN_H
#define AERO_KALMAN_H

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    double x[2];     /* altitude m, vertical velocity m/s */
    double P[2][2];
    double accel_sigma;
    double baro_sigma;
} aero_kf_t;

void aero_kf_init(aero_kf_t *kf, double accel_sigma, double baro_sigma, double alt0);
void aero_kf_predict(aero_kf_t *kf, double a_vertical, double dt, double accel_sigma);
/* Returns the normalized innovation of the update. */
double aero_kf_update(aero_kf_t *kf, double baro_alt, double baro_sigma);
double aero_kf_innovation(const aero_kf_t *kf, double baro_alt, double baro_sigma);
void aero_kf_set_state(aero_kf_t *kf, double alt, double vel);

#ifdef __cplusplus
}
#endif
#endif
