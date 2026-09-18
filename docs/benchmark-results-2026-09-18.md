# Hasil benchmark deployment Laravel: Tailscale SSH vs self-hosted runner

Tanggal pengukuran: 18 September 2026. Semua angka berasal dari tiga run sukses per metode terhadap commit aplikasi yang sama, `7011d0f9380379196ad558cc6f55da345bbbfc57`. Setiap run melakukan satu deployment prapengondisian yang tidak dihitung, lalu warm-up, baseline idle 60 detik, deployment terukur, dan recovery 15 detik.

## Ringkasan eksekutif

- **Self-hosted runner 4,82× lebih cepat**: wall time Deployer `9,78 ± 0,39 s`, dibanding Tailscale SSH `47,11 ± 5,49 s`. Pengurangan waktu rata-rata: **79,25%**.
- **Tailscale lebih ringan bagi target saat deploy**: target rata-rata memakai `13,56 ± 1,23%` kapasitas CPU dan `424,14 ± 14,85 MiB` RAM; runner memakai `45,69 ± 1,65%` CPU dan `526,00 ± 12,22 MiB` RAM.
- **Peak RAM target**: Tailscale `464,47 ± 12,02 MiB` (55,5% dari RAM VM), runner `566,84 ± 16,36 MiB` (67,8%). Headroom peak kira-kira 372 MiB vs 269 MiB.
- **PSS seluruh proses target ketika deploy**: Tailscale `371,13 ± 9,13 MiB`; runner `481,44 ± 12,95 MiB`.
- **CPU lebih bursty pada runner**, tetapi selesai jauh lebih cepat. Setelah baseline dikurangkan, runner mengonsumsi `7,811 ± 0,063 core-s`; Tailscale mengonsumsi `12,458 ± 0,492 core-s` bila controller hosted dan target dijumlahkan. Angka lintas-host ini indikatif karena model CPU kedua host berbeda.
- Kesimpulan operasional: pilih **runner** untuk kecepatan dan efisiensi waktu total; pilih **Tailscale SSH** bila headroom CPU/RAM di target kecil lebih penting daripada durasi.

## Run dan lingkungan

### Run yang dipakai

Tailscale SSH:

- [35304148438](https://github.com/hasbimizan/tailscaling/actions/runs/35304148438)
- [35305050323](https://github.com/hasbimizan/tailscaling/actions/runs/35305050323)
- [35305326187](https://github.com/hasbimizan/tailscaling/actions/runs/35305326187)

Self-hosted runner:

- [35304457518](https://github.com/hasbimizan/tailscaling/actions/runs/35304457518)
- [35304890872](https://github.com/hasbimizan/tailscaling/actions/runs/35304890872)
- [35305634675](https://github.com/hasbimizan/tailscaling/actions/runs/35305634675)

Run awal `35303891081` dikeluarkan karena target saat itu belum memiliki PHP dan sampler belum dimulai. Runtime dipasang melalui [provisioning run 35304052259](https://github.com/hasbimizan/tailscaling/actions/runs/35304052259).

### Mesin

| Peran | OS/kernel | CPU efektif | RAM | CPU model |
|---|---|---:|---:|---|
| GitHub-hosted controller Tailscale | Ubuntu 24.04.5 / 6.17.0-1022-azure | 4 | 15.990 MiB | AMD EPYC 9V45 |
| Target + self-hosted runner | Ubuntu 26.04.1 / 7.0.0-1012-azure | 2 | 836,19 MiB | AMD EPYC 7763 |

Target menggunakan PHP 8.5.4, Composer 2.9.5, Python 3.14.4, Git 2.53.0, dan Tailscale daemon. Laravel Framework yang terkunci adalah 12.69.2.

## Statistik host antarrun

Format: **mean ± sample SD [min–max]**. CPU adalah persentase kapasitas seluruh host; RAM adalah `MemTotal - MemAvailable`; PSS adalah jumlah PSS proses yang dapat dibaca.

### Target: Tailscale SSH

| Metrik | Idle | Deploy |
|---|---:|---:|
| Durasi terobservasi | 61,667 ± 0,231 s [61,400–61,800] | 48,933 ± 5,749 s [42,800–54,200] |
| CPU host | 3,552 ± 0,071% [3,473–3,609] | 13,560 ± 1,231% [12,516–14,917] |
| Busy cores | 0,071 ± 0,001 | 0,269 ± 0,024 |
| RAM rata-rata | 402,165 ± 18,524 MiB | 424,135 ± 14,847 MiB |
| RAM p95 | 404,725 ± 18,592 MiB | 449,301 ± 12,124 MiB |
| RAM peak | 406,449 ± 18,236 MiB | 464,473 ± 12,020 MiB |
| PSS proses rata-rata | 366,858 ± 15,328 MiB | 371,125 ± 9,132 MiB |
| PSS proses peak | 368,198 ± 16,864 MiB | 423,940 ± 10,589 MiB |

Delta deploy minus idle: `+10,008 ± 1,194` poin persentase CPU, `+21,970 ± 3,677 MiB` RAM, dan `+4,267 ± 6,460 MiB` PSS rata-rata.

### Controller hosted: Tailscale SSH

| Metrik | Idle | Deploy |
|---|---:|---:|
| Durasi terobservasi | 61,733 ± 0,231 s | 48,800 ± 5,765 s |
| CPU host | 1,500 ± 0,461% | 2,971 ± 0,532% |
| RAM rata-rata | 1.013,381 ± 40,081 MiB | 1.040,852 ± 43,573 MiB |
| RAM peak | 1.017,508 ± 41,944 MiB | 1.052,742 ± 47,128 MiB |
| PSS proses rata-rata | 702,021 ± 37,846 MiB | 752,291 ± 41,564 MiB |
| PSS proses peak | 704,188 ± 38,693 MiB | 759,241 ± 38,936 MiB |

Delta deploy minus idle: `+1,472 ± 0,128` poin CPU, `+27,471 ± 4,067 MiB` RAM, dan `+50,271 ± 3,957 MiB` PSS.

### Target sekaligus self-hosted runner

| Metrik | Idle | Deploy |
|---|---:|---:|
| Durasi terobservasi | 59,800 ± 0,000 s | 9,467 ± 0,416 s [9,000–9,800] |
| CPU host | 3,969 ± 0,256% | 45,692 ± 1,648% |
| Busy cores | 0,079 ± 0,005 | 0,905 ± 0,034 |
| RAM rata-rata | 476,515 ± 15,419 MiB | 525,997 ± 12,222 MiB |
| RAM p95 | 483,740 ± 18,444 MiB | 550,198 ± 15,235 MiB |
| RAM peak | 483,953 ± 18,571 MiB | 566,837 ± 16,363 MiB |
| PSS proses rata-rata | 438,775 ± 13,856 MiB | 481,440 ± 12,950 MiB |
| PSS proses peak | 441,543 ± 16,180 MiB | 500,289 ± 17,540 MiB |

Delta deploy minus idle: `+41,723 ± 1,721` poin CPU, `+49,482 ± 5,191 MiB` RAM, dan `+42,665 ± 3,656 MiB` PSS.

Baseline RAM runner lebih tinggi terutama karena `Runner.Worker` aktif di mesin target. Pada jalur Tailscale, target hanya menjalankan listener runner yang idle; controller pekerjaan berada di VM GitHub-hosted.

## Semua komponen proses

Angka berikut adalah mean dari tiga **mean per-run**. CPU adalah `% satu logical core` sehingga dapat melebihi 100%; RAM menggunakan PSS time-weighted. `php_cli` terutama merupakan proses Deployer PHAR/worker yang tidak termasuk Composer atau Artisan. `other` mencakup OS, Azure agent, kernel/user services yang tidak diberi kategori khusus.

### Tailscale: GitHub-hosted controller

| Komponen | CPU idle | CPU deploy | PSS idle MiB | PSS deploy MiB | PSS peak deploy MiB | RSS deploy MiB | USS deploy MiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| github_actions_runner | 0,31 | 0,62 | 184,23 | 185,67 | 186,78 | 253,05 | 141,13 |
| metrics_sampler | 5,50 | 5,88 | 11,52 | 11,88 | 11,95 | 22,85 | 10,05 |
| other | 0,42 | 0,46 | 368,91 | 361,97 | 369,18 | 484,64 | 316,74 |
| php_cli / Deployer | 0,00 | 0,07 | 0,00 | 0,23 | 8,64 | 0,46 | 0,12 |
| php_fpm bawaan image | 0,00 | 0,00 | 41,32 | 33,46 | 41,09 | 92,20 | 12,33 |
| shell | 0,00 | 0,00 | 4,09 | 3,83 | 3,89 | 14,08 | 1,99 |
| ssh | 0,00 | 0,07 | 1,31 | 3,39 | 3,70 | 10,62 | 2,42 |
| system_service | 0,00 | 0,01 | 27,47 | 27,19 | 27,41 | 64,56 | 21,26 |
| tailscale | 0,09 | 3,35 | 63,15 | 124,66 | 127,54 | 182,39 | 99,48 |

`/usr/bin/time -v` lebih representatif untuk tree controller Deployer yang berumur pendek: max RSS `60,47 ± 0,09 MiB`, user CPU `1,50 ± 0,23 s`, system CPU `0,92 ± 0,19 s`.

### Tailscale: target

| Komponen | CPU idle | CPU deploy | PSS idle MiB | PSS deploy MiB | PSS peak deploy MiB | RSS deploy MiB | USS deploy MiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| composer | 0,00 | 3,82 | 0,00 | 3,16 | 49,45 | 3,94 | 2,88 |
| git | 0,00 | <0,01 | 0,00 | 0,08 | 3,38 | 0,20 | 0,06 |
| github_actions_runner listener | 0,00 | 0,01 | 96,27 | 96,13 | 96,28 | 107,54 | 92,97 |
| laravel_artisan | 0,00 | 1,13 | 0,00 | 1,52 | 39,64 | 2,00 | 1,32 |
| metrics_sampler | 6,46 | 7,25 | 15,68 | 15,65 | 15,71 | 23,75 | 12,95 |
| node helper | 0,00 | 0,00 | 12,14 | 11,91 | 12,14 | 14,10 | 11,70 |
| other | 0,65 | 2,18 | 160,28 | 159,94 | 168,36 | 261,57 | 141,71 |
| shell | 0,00 | 0,00 | 2,81 | 2,89 | 3,72 | 6,19 | 2,15 |
| ssh | 0,00 | 0,01 | 1,93 | 1,91 | 1,93 | 7,50 | 1,38 |
| system_service | 0,00 | 1,73 | 28,87 | 29,11 | 29,33 | 64,59 | 24,10 |
| tailscale | 0,12 | 0,94 | 48,89 | 48,81 | 58,52 | 51,74 | 45,90 |

### Self-hosted runner + target

| Komponen | CPU idle | CPU deploy | PSS idle MiB | PSS deploy MiB | PSS peak deploy MiB | RSS deploy MiB | USS deploy MiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| composer | 0,00 | 20,29 | 0,00 | 15,23 | 39,89 | 23,78 | 12,49 |
| git | 0,00 | <0,01 | 0,00 | <0,01 | <0,01 | <0,01 | <0,01 |
| github_actions_runner | 0,43 | 3,14 | 178,53 | 169,95 | 177,43 | 210,43 | 150,24 |
| laravel_artisan | 0,00 | 9,90 | 0,00 | 9,22 | 48,51 | 16,50 | 6,69 |
| metrics_sampler | 6,91 | 10,36 | 15,72 | 15,28 | 15,62 | 23,54 | 12,59 |
| node helper | 0,00 | 0,00 | 6,17 | 6,06 | 6,11 | 8,36 | 5,94 |
| other | 0,91 | 5,06 | 154,98 | 156,90 | 158,34 | 260,40 | 140,79 |
| php_cli / Deployer | 0,00 | 8,38 | 0,00 | 35,57 | 46,15 | 65,96 | 24,11 |
| shell | 0,00 | 0,00 | 9,61 | 4,37 | 4,63 | 16,45 | 3,05 |
| ssh daemon idle | 0,01 | 0,00 | 1,89 | 1,78 | 1,83 | 7,51 | 1,39 |
| system_service | 0,01 | 0,03 | 28,38 | 27,94 | 28,16 | 63,49 | 23,36 |
| tailscale daemon (bukan jalur deploy) | 0,04 | 0,07 | 43,49 | 39,13 | 41,57 | 39,14 | 39,13 |

Pada metode runner, Tailscale daemon tetap hidup sebagai bagian konfigurasi VM tetapi tidak membawa deployment.

## Biaya Tailscale secara khusus

Gabungan PSS kategori Tailscale pada controller dan target rata-rata:

- Idle: `63,15 + 48,89 = 112,04 MiB`.
- Deploy: `124,66 + 48,81 = 173,47 MiB`.
- Kenaikan mean selama deploy: sekitar **61,43 MiB**, hampir seluruhnya pada `tailscaled` GitHub-hosted.
- CPU: `0,21%` satu core ketika idle menjadi sekitar `4,29%` satu core ketika deploy jika kedua host dijumlahkan.

Contoh snapshot mentah yang ekuivalen dengan `sudo cat /proc/<PID>/smaps_rollup`:

| Proses/snapshot | RSS | PSS | USS (private clean+dirty) | SwapPSS |
|---|---:|---:|---:|---:|
| Hosted `tailscaled` PID 2415 | 57.888 KiB | 57.880 KiB | 57.880 KiB | 0 |
| Hosted wrapper `sudo` PID 2413 | 6.940 KiB | 1.866 KiB | 1.212 KiB | 0 |
| Target `tailscaled` PID 799 | 38.244 KiB | 30.748 KiB | 23.260 KiB | 0 |
| Target Tailscale SSH process PID 12136 | 22.060 KiB | 14.564 KiB | 7.076 KiB | 0 |
| Runner.Listener PID 2071 | 106.920 KiB | 86.880 KiB | 75.248 KiB | 0 |
| Runner.Worker PID 15055 | 110.840 KiB | 89.771 KiB | 77.292 KiB | 0 |

Snapshot ini berasal dari run pertama sebelum sampling; angka kontinu time-weighted pada tabel komponen adalah dasar perbandingan utama.

## Overhead dan kualitas pengukuran

- Interval CPU/proses: 200 ms; interval `smaps_rollup`: 1 detik.
- Gap sample maksimum antarrun: hosted Tailscale `0,2002 ± 0,0002 s`, target Tailscale `0,2009 ± 0,0004 s`, runner `0,2043 ± 0,0045 s`.
- Total pembacaan `smaps_rollup` sukses: **40.051**; permission denied: **0**.
- Terdapat 103.295 hasil `ESRCH/no-mm`, didominasi kernel thread (`kthreadd`, `kworker`, `migration`, dan sejenisnya) yang memang tidak memiliki userspace memory map; proses tersebut tidak menambah PSS userspace. Ada 251 race saat membaca `/proc/<PID>/stat` dari proses sangat singkat.
- Sampler sendiri memakai sekitar 15–16 MiB PSS dan 5,5–10,4% satu core. Karena itu laporan mempertahankan kategori sampler, bukan menyembunyikannya.

Perkiraan CPU host setelah CPU sampler dikurangkan langsung:

| Peran | Idle | Deploy |
|---|---:|---:|
| Tailscale hosted controller | 0,123 ± 0,068% kapasitas | 1,493 ± 0,040% |
| Tailscale target | 0,313 ± 0,112% | 9,817 ± 1,102% |
| Runner-target | 0,502 ± 0,204% | 40,078 ± 1,479% |

Pengurangan ini tidak dapat membatalkan efek sekunder scanning `/proc` pada cache/scheduler, jadi angka raw dan adjusted sama-sama dicantumkan.

## Verifikasi deployment aktual

[Verification run 35305952741](https://github.com/hasbimizan/tailscaling/actions/runs/35305952741) membaca target secara langsung dan lulus seluruh assertion:

- `current` menunjuk `/home/mijon/apps/tailscaling/releases/12`.
- `REVISION` dan `DEPLOYMENT` sama-sama berisi `7011d0f9380379196ad558cc6f55da345bbbfc57`.
- Metadata terakhir: `method=runner`, `run_id=35305634675-1`, deployed at `2026-09-18T04:07:18Z`.
- Laravel 12.69.2: environment `production`, debug `OFF`, maintenance `OFF`.
- Tiga migration starter berstatus `Ran`.
- Route `GET|HEAD /up` tersedia.
- SQLite `PRAGMA integrity_check=ok`.
- `.env` mode `0600`, shared database directory `0700`, SQLite file `0600`.
- Symlink shared storage, SQLite, dan `public/storage` valid.

Aplikasi sudah terdeploy secara atomik, tetapi repository ini tidak memprovisikan web server/PHP-FPM pada target. Idle yang diukur adalah biaya platform/deployment agent, bukan resident Laravel web worker.

## Interpretasi dan batasan

1. Angka adalah tiga run warm-state, cukup untuk mean/SD awal tetapi belum merupakan confidence interval kuat. Untuk keputusan kapasitas produksi, lanjutkan 10+ run dan tambahkan beban eksternal yang terkendali.
2. Tailscale memindahkan Deployer/controller ke host lain; CPU/RAM target lebih rendah, tetapi resource total dua host lebih besar.
3. Runner bekerja lokal sehingga tidak membayar puluhan SSH round trip per task. Itulah penyebab utama speedup 4,82×.
4. Baseline antar metode tidak identik secara absolut: Runner.Worker hanya aktif pada metode runner, dan heap Tailscale daemon berubah antarwaktu. Delta deploy-idle dan preconditioning digunakan untuk mengurangi bias ini.
5. Proses Composer/Git/Artisan yang sangat singkat dapat berada di antara snapshot RAM 1 detik. Peak yang tertangkap adalah lower bound; CPU cgroup dan `/usr/bin/time -v` menjadi cross-check untuk process tree yang singkat.
6. `cgroup memory.current` tidak dibandingkan lintas metode karena cgroup target Tailscale hanya mencakup session sampler, sedangkan cgroup runner mencakup service runner dan seluruh child lokal.
