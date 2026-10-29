"""
prepare_arepo.py  --  turn a CATS AREPO uniform-grid snapshot into FITS cubes
that rotate_maps.py can read.

  1. Reads the 512^3 HDF5 files a slab at a time (never the whole 4 GB at once)
     and block-averages them down by --factor (default 2 -> 256^3, the same
     resolution as the Cho-ENO runs).
  2. Works out which of MagneticField_0/1/2 points along which array direction,
     using div B = 0: only the correct pairing gives a near-zero divergence.
  3. Writes dens.fits, magx.fits, magy.fits, magz.fits (float32) in --out,
     labelled with the same convention as firstmap_b.py
     (array axis 2 = x, axis 1 = y, axis 0 = z).

Usage:
    python prepare_arepo.py \
        --dens arepo_B100/Density_B100snap_156_512.hdf5 \
        --mag  arepo_B100/MagneticField_B100snap_156_512.hdf5 \
        --out  arepo_B100/grid256

    In Jupyter: %run prepare_arepo.py --dens ... --mag ... --out ...

Then:
    %run rotate_maps.py --rho arepo_B100/grid256/dens.fits \
        --bx arepo_B100/grid256/magx.fits --by arepo_B100/grid256/magy.fits \
        --bz arepo_B100/grid256/magz.fits --run AREPO_B100_snap156

Self-test (no downloads): python prepare_arepo.py --selftest
"""

import argparse
import itertools
import os
import time

import numpy as np

AXIS_LABEL = {0: "z", 1: "y", 2: "x"}      # same convention as firstmap_b.py


def downsample_dataset(dset, f):
    """Block-average an (n, n, n) HDF5 dataset by factor f, reading f planes at a time."""
    n0, n1, n2 = dset.shape
    if n0 % f or n1 % f or n2 % f:
        raise ValueError(f"shape {dset.shape} not divisible by factor {f}")
    out = np.empty((n0 // f, n1 // f, n2 // f), dtype=np.float64)
    for i in range(n0 // f):
        slab = np.asarray(dset[i * f:(i + 1) * f], dtype=np.float64)   # (f, n1, n2)
        out[i] = slab.reshape(f, n1 // f, f, n2 // f, f).mean(axis=(0, 2, 4))
    return out


def mean_abs_div(comps, axes):
    """mean |div B| with component comps[i] differentiated along array axis axes[i]."""
    d = lambda a, ax: (np.roll(a, -1, ax) - np.roll(a, 1, ax)) / 2.0
    return float(np.abs(sum(d(c, ax) for c, ax in zip(comps, axes))).mean())


def match_components(comps):
    """Find which array axis each field component lies along, by minimising div B."""
    scores = sorted((mean_abs_div(comps, perm), perm)
                    for perm in itertools.permutations((0, 1, 2)))
    (best, perm), (second, _) = scores[0], scores[1]
    return perm, best, second, scores


def prepare(dens_file, mag_file, out_dir, factor=2, verbose=True):
    import h5py
    from astropy.io import fits

    t0 = time.time()
    with h5py.File(dens_file, "r") as h:
        rho = downsample_dataset(h["Density"], factor)
    if verbose:
        print(f"density: {rho.shape}  ({time.time() - t0:.0f} s)")
    with h5py.File(mag_file, "r") as h:
        comps = [downsample_dataset(h[f"MagneticField_{i}"], factor) for i in range(3)]
    if verbose:
        print(f"field:   3 x {comps[0].shape}  ({time.time() - t0:.0f} s)")

    perm, best, second, scores = match_components(comps)
    if verbose:
        print("\nmean |div B| for each pairing (component 0,1,2 -> array axis):")
        for s, p in scores:
            print(f"   {p}  {s:.3e}{'   <- chosen' if p == perm else ''}")
        print(f"best is {second / best:.1f}x smaller than the next pairing")
    if second / best < 3:
        print("WARNING: the pairing is not clear-cut (ratio < 3). Check before trusting it.")

    B = {AXIS_LABEL[ax]: comps[i] for i, ax in enumerate(perm)}
    mapping = {f"MagneticField_{i}": AXIS_LABEL[ax] for i, ax in enumerate(perm)}

    os.makedirs(out_dir, exist_ok=True)
    note = f"CATS AREPO, block-averaged x{factor}"
    for name, arr in [("dens", rho), ("magx", B["x"]), ("magy", B["y"]), ("magz", B["z"])]:
        hdu = fits.PrimaryHDU(arr.astype(np.float32))
        hdu.header["HISTORY"] = note
        hdu.header["HISTORY"] = f"source: {os.path.basename(dens_file if name == 'dens' else mag_file)}"
        hdu.writeto(os.path.join(out_dir, f"{name}.fits"), overwrite=True)

    if verbose:
        print("\ncomponent labels:", mapping)
        print("mean field:  " + "  ".join(f"<B{k}> = {B[k].mean():+.4g}" for k in "xyz"))
        print("rms field:   " + "  ".join(f"rms B{k} = {B[k].std():.4g}" for k in "xyz"))
        print(f"density: mean {rho.mean():.4g}  max/mean {rho.max() / rho.mean():.3g}  "
              f"sigma/mean {rho.std() / rho.mean():.3g}")
        print(f"\nwrote dens/magx/magy/magz.fits to {out_dir}  ({time.time() - t0:.0f} s total)")
    return mapping


# ---------------------------------------------------------------- self-test
def selftest():
    """Build a divergence-free field, scramble its components in an HDF5 file,
    and check prepare() puts them back correctly."""
    import tempfile
    import h5py
    from astropy.io import fits

    n, rng = 64, np.random.default_rng(1)
    d = lambda a, ax: (np.roll(a, -1, ax) - np.roll(a, 1, ax)) / 2.0
    k = np.fft.fftfreq(n)
    kk = np.sqrt(k[:, None, None]**2 + k[None, :, None]**2 + k[None, None, :]**2)
    kk[0, 0, 0] = 1
    smooth = lambda: np.real(np.fft.ifftn(np.fft.fftn(rng.standard_normal((n, n, n))) * kk**-2.5))
    Az, Ay, Ax = smooth(), smooth(), smooth()          # vector potential on axes 0,1,2
    # B = curl A in array-axis components (axis0, axis1, axis2); div is exactly 0
    B0 = d(Ax, 1) - d(Ay, 2)
    B1 = d(Az, 2) - d(Ax, 0)
    B2 = d(Ay, 0) - d(Az, 1)
    s = 0.3 / np.std(B2)
    B0, B1, B2 = B0 * s, B1 * s, B2 * s + 1.0           # mean field along axis 2 (= x)
    assert mean_abs_div([B0, B1, B2], (0, 1, 2)) < 1e-12

    with tempfile.TemporaryDirectory() as tmp:
        dens_f, mag_f = os.path.join(tmp, "d.hdf5"), os.path.join(tmp, "m.hdf5")
        with h5py.File(dens_f, "w") as h:
            h["Density"] = np.exp(0.5 * smooth() / smooth().std())
        with h5py.File(mag_f, "w") as h:              # stored in a scrambled order
            h["MagneticField_0"] = B2                   # along axis 2 -> x
            h["MagneticField_1"] = B0                   # along axis 0 -> z
            h["MagneticField_2"] = B1                   # along axis 1 -> y
        mapping = prepare(dens_f, mag_f, os.path.join(tmp, "out"), factor=2, verbose=False)
        assert mapping == {"MagneticField_0": "x", "MagneticField_1": "z",
                           "MagneticField_2": "y"}, mapping
        bx = fits.getdata(os.path.join(tmp, "out", "magx.fits"))
        assert bx.shape == (32, 32, 32) and abs(bx.mean() - 1.0) < 1e-6
    print("[check] scrambled field components recovered from div B = 0   OK")
    print("[check] 64^3 -> 32^3 block average, mean field preserved      OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dens"); ap.add_argument("--mag"); ap.add_argument("--out")
    ap.add_argument("--factor", type=int, default=2)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    if not all([a.dens, a.mag, a.out]):
        ap.error("give --dens --mag --out, or --selftest")
    prepare(a.dens, a.mag, a.out, a.factor)


if __name__ == "__main__":
    main()
