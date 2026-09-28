#include "aero_kalman.h"

#include <math.h>

void aero_kf_init(aero_kf_t *kf, double accel_sigma, double baro_sigma, double alt0)
{
    kf->x[0] = alt0;
    kf->x[1] = 0.0;
    kf->P[0][0] = 10.0;
    kf->P[0][1] = 0.0;
    kf->P[1][0] = 0.0;
    kf->P[1][1] = 1.0;
    kf->accel_sigma = accel_sigma;
    kf->baro_sigma = baro_sigma;
}

void aero_kf_set_state(aero_kf_t *kf, double alt, double vel)
{
    kf->x[0] = alt;
    kf->x[1] = vel;
}

void aero_kf_predict(aero_kf_t *kf, double a, double dt, double accel_sigma)
{
    double s = accel_sigma > 0.0 ? accel_sigma : kf->accel_sigma;
    double b0 = 0.5 * dt * dt, b1 = dt;
    kf->x[0] += dt * kf->x[1] + b0 * a;
    kf->x[1] += b1 * a;
    /* P = F P F^T + G G^T s^2, F = [[1 dt][0 1]] */
    double p00 = kf->P[0][0], p01 = kf->P[0][1], p10 = kf->P[1][0], p11 = kf->P[1][1];
    double q = s * s;
    kf->P[0][0] = p00 + dt * (p10 + p01) + dt * dt * p11 + b0 * b0 * q;
    kf->P[0][1] = p01 + dt * p11 + b0 * b1 * q;
    kf->P[1][0] = p10 + dt * p11 + b0 * b1 * q;
    kf->P[1][1] = p11 + b1 * b1 * q;
}

double aero_kf_innovation(const aero_kf_t *kf, double z, double baro_sigma)
{
    double r = baro_sigma > 0.0 ? baro_sigma : kf->baro_sigma;
    return (z - kf->x[0]) / sqrt(kf->P[0][0] + r * r);
}

double aero_kf_update(aero_kf_t *kf, double z, double baro_sigma)
{
    double r = baro_sigma > 0.0 ? baro_sigma : kf->baro_sigma;
    double y = z - kf->x[0];
    double s = kf->P[0][0] + r * r;
    double k0 = kf->P[0][0] / s, k1 = kf->P[1][0] / s;
    kf->x[0] += k0 * y;
    kf->x[1] += k1 * y;
    double p00 = kf->P[0][0], p01 = kf->P[0][1];
    kf->P[0][0] -= k0 * p00;
    kf->P[0][1] -= k0 * p01;
    kf->P[1][0] -= k1 * p00;
    kf->P[1][1] -= k1 * p01;
    return y / sqrt(s);
}
