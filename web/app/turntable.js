import * as T from "./vendor/three.module.min.js";

function canvasTexture(width, height, paint) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  paint(canvas.getContext("2d"), width, height);
  const map = new T.CanvasTexture(canvas);
  map.colorSpace = T.SRGBColorSpace;
  map.anisotropy = 4;
  return map;
}
function noise(seed) {
  return () => {
    seed = (1664525 * seed + 1013904223) >>> 0;
    return seed / 4294967296;
  };
}
function timber() {
  return canvasTexture(1536, 512, (c, w, h) => {
    const rand = noise(73),
      img = c.createImageData(w, h);
    for (let y = 0; y < h; y++) {
      const grain = rand() * 0.7;
      for (let x = 0; x < w; x++) {
        const v =
          y +
          2.5 * Math.sin(x * 0.005 + y * 0.007) +
          2 * Math.sin(x * 0.01 - y * 0.014) +
          5 * Math.sin(x * 0.0028 + y * 0.021);
        const b =
          10 * Math.sin(v * 0.03) +
          3 * Math.sin(v * 0.7) +
          2 * Math.sin(v * 1.33) +
          3 * Math.sin(v * 0.17) +
          grain * 2 +
          3 * rand();
        const i = (y * w + x) * 4;
        img.data[i] = 105 + b;
        img.data[i + 1] = 66 + b * 0.77;
        img.data[i + 2] = 42 + b * 0.53;
        img.data[i + 3] = 255;
      }
    }
    c.putImageData(img, 0, 0);
    for (let i = 0; i < 900; i++) {
      const x = rand() * w,
        y = rand() * h,
        l = 18 + rand() * 270;
      c.strokeStyle = "rgba(40,19,8," + (0.02 + rand() * 0.24) + ")";
      c.lineWidth = 0.5 + rand() * 0.6;
      c.beginPath();
      c.moveTo(x, y);
      c.quadraticCurveTo(
        x + l * 0.6,
        y + rand() * 5 - 2,
        x + l,
        y + rand() * 3,
      );
      c.stroke();
    }
  });
}
function vinyl() {
  return canvasTexture(1536, 1536, (c, w, h) => {
    const img = c.createImageData(w, h),
      rand = noise(14);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const xx = (x - w / 2) / (w / 2),
          yy = (y - h / 2) / (h / 2),
          r = Math.hypot(xx, yy),
          a = Math.atan2(yy, xx);
        const shine = Math.pow(Math.max(0, Math.cos((a + 0.94) * 2)), 6);
        const soft = Math.pow(Math.max(0, Math.cos((a - 0.51) * 2)), 3) * 0.28;
        const groove =
          0.38 + 0.62 * Math.pow(0.5 + 0.5 * Math.cos(r * 1140), 0.6);
        const valley =
          (r > 0.48 && r < 0.491) ||
          (r > 0.636 && r < 0.645) ||
          (r > 0.804 && r < 0.814) ||
          (r > 0.958 && r < 0.966);
        const l =
          (valley ? 0.22 : groove) *
            (7 + 109 * shine + 35 * soft) *
            (1 + Math.cos(r * 111) * 0.08) +
          rand() * 2;
        const i = (y * w + x) * 4;
        img.data[i] = 12 + l * 0.94;
        img.data[i + 1] = 14 + l * 0.96;
        img.data[i + 2] = 16 + l;
        img.data[i + 3] = 255;
      }
    c.putImageData(img, 0, 0);
  });
}
function recordLabel() {
  return canvasTexture(512, 512, (c, w, h) => {
    const rand = noise(61);
    c.fillStyle = "#cca379";
    c.fillRect(0, 0, w, h);
    for (let i = 0; i < 28000; i++) {
      c.fillStyle = rand() > 0.5 ? "#f4dfb323" : "#4936221a";
      c.fillRect(rand() * w, rand() * h, 1.5, 1.5);
    }
    c.strokeStyle = "#65462c";
    c.lineWidth = 2;
    c.beginPath();
    c.arc(256, 256, 225, 0, Math.PI * 2);
    c.stroke();
    c.fillStyle = "#392c22";
    c.textAlign = "center";
    c.font = "bold italic 48px Georgia";
    c.fillText("Djound", 256, 156);
    c.font = "18px Arial";
    c.fillText("T O   Z A N A R K A N D", 256, 344);
    c.font = "13px Arial";
    c.fillText("N O B U O   U E M A T S U", 256, 372);
    c.fillRect(99, 237, 80, 2);
    c.fillRect(333, 237, 80, 2);
    c.font = "16px Arial";
    c.fillText("33 ⅓", 138, 265);
    c.fillText("SIDE A", 373, 265);
  });
}
function posterArt() {
  return canvasTexture(640, 640, (c, w, h) => {
    c.fillStyle = "#a59c88";
    c.fillRect(0, 0, w, h);
    c.fillStyle = "#332f32";
    c.beginPath();
    c.ellipse(335, 367, 222, 226, 0, 0, 7);
    c.fill();
    c.fillStyle = "#8f452d";
    c.beginPath();
    c.ellipse(395, 315, 152, 158, 0, 0, 7);
    c.fill();
    c.fillStyle = "#c99f6b";
    c.beginPath();
    c.ellipse(421, 282, 96, 94, 0, 0, 7);
    c.fill();
    c.fillStyle = "#373431";
    c.font = "italic 33px Georgia";
    c.fillText("Evening sessions", 37, 57);
    const rand = noise(979);
    for (let i = 0; i < 80000; i++) {
      c.fillStyle = rand() > 0.5 ? "#160e0917" : "#eee4c717";
      c.fillRect(rand() * w, rand() * h, 1.5, 1.5);
    }
  });
}
function reflectionStudio() {
  return canvasTexture(512, 256, (c, w, h) => {
    const grd = c.createLinearGradient(0, 0, 0, h);
    grd.addColorStop(0, "#5c5045");
    grd.addColorStop(0.46, "#c2a386");
    grd.addColorStop(0.52, "#392a20");
    grd.addColorStop(1, "#17110e");
    c.fillStyle = grd;
    c.fillRect(0, 0, w, h);
    c.fillStyle = "#ffe1a3";
    c.fillRect(20, 35, 67, 72);
    c.fillStyle = "#f6e9d8";
    c.fillRect(245, 56, 25, 82);
    c.fillStyle = "#a2a8a5";
    c.fillRect(413, 82, 29, 64);
  });
}

export function mountRoom(canvas, setRecordHitTest) {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");

  if (reduced.matches) {
    reduced.addEventListener("change", function resume() {
      if (!reduced.matches) {
        reduced.removeEventListener("change", resume);
        mountRoom(canvas, setRecordHitTest);
      }
    });
    return;
  }
  const renderer = new T.WebGLRenderer({
    canvas,
    alpha: false,
    antialias: true,
    powerPreference: "low-power",
  });
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFSoftShadowMap;
  renderer.outputColorSpace = T.SRGBColorSpace;
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.03;
  const scene = new T.Scene();
  scene.background = new T.Color(0x28251f);
  scene.fog = new T.FogExp2(0x28251f, 0.048);
  const camera = new T.PerspectiveCamera(36, 1, 0.05, 100);
  const env = reflectionStudio();
  env.mapping = T.EquirectangularReflectionMapping;
  scene.environment = env;
  scene.environmentIntensity = 0.9;
  const standard = (color, metalness = 0, roughness = 0.65, map = null) =>
    new T.MeshStandardMaterial({ color, metalness, roughness, map });
  const woodMap = timber(),
    wood = standard(0xeed0ac, 0, 0.38, woodMap),
    walnut = standard(0xbea88d, 0, 0.68, woodMap),
    ebony = standard(0x222322, 0, 0.47),
    rubber = standard(0x171918, 0, 0.92),
    steel = standard(0xc4c4bc, 0.89, 0.22),
    brass = standard(0xbdaa80, 0.72, 0.29),
    darkMetal = standard(0x555450, 0.63, 0.37),
    blackMetal = standard(0x353735, 0.6, 0.34);
  function mesh(parent, geo, mat, x = 0, y = 0, z = 0) {
    const obj = new T.Mesh(geo, mat);
    obj.position.set(x, y, z);
    obj.castShadow = true;
    obj.receiveShadow = true;
    parent.add(obj);
    return obj;
  }
  function box(p, w, h, d, m, x, y, z) {
    return mesh(p, new T.BoxGeometry(w, h, d), m, x, y, z);
  }
  function cyl(p, rTop, rBottom, h, m, x, y, z, n = 80) {
    return mesh(p, new T.CylinderGeometry(rTop, rBottom, h, n), m, x, y, z);
  }
  function roundBox(p, w, h, d, r, m, x, y, z) {
    const s = new T.Shape();
    const l = -w / 2,
      t = -d / 2,
      R = w / 2,
      B = d / 2;
    s.moveTo(l + r, t);
    s.lineTo(R - r, t);
    s.quadraticCurveTo(R, t, R, t + r);
    s.lineTo(R, B - r);
    s.quadraticCurveTo(R, B, R - r, B);
    s.lineTo(l + r, B);
    s.quadraticCurveTo(l, B, l, B - r);
    s.lineTo(l, t + r);
    s.quadraticCurveTo(l, t, l + r, t);
    const g = new T.ExtrudeGeometry(s, {
      depth: h,
      bevelEnabled: true,
      bevelSegments: 2,
      steps: 1,
      bevelSize: 0.017,
      bevelThickness: 0.012,
      curveSegments: 8,
    });
    g.rotateX(-Math.PI / 2);
    const uvs = g.attributes.uv,
      pos = g.attributes.position,
      normal = g.attributes.normal;
    for (let i = 0; i < pos.count; i++) {
      const u =
        (Math.abs(normal.getX(i)) > 0.8 ? pos.getZ(i) / d : pos.getX(i) / w) +
        0.5;
      const v =
        Math.abs(normal.getY(i)) > 0.7
          ? pos.getZ(i) / d + 0.5
          : pos.getY(i) / (h + 0.02);
      uvs.setXY(i, u, v);
    }
    return mesh(p, g, m, x, y, z);
  }
  function rod(p, a, b, r, m) {
    const A = new T.Vector3(...a),
      B = new T.Vector3(...b),
      v = B.clone().sub(A);
    const o = cyl(p, r, r, v.length(), m, 0, 0, 0, 20);
    o.position.copy(A.add(B).multiplyScalar(0.5));
    o.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), v.normalize());
    return o;
  }
  function disc(p, r, m, x, y, z) {
    const g = new T.CircleGeometry(r, 144);
    g.rotateX(-Math.PI / 2);
    return mesh(p, g, m, x, y, z);
  }
  const base = new T.Group();
  scene.add(base);
  box(scene, 48, 0.45, 18, walnut, 0, -0.64, -0.8);
  box(scene, 47, 16, 0.3, standard(0x514039), 0, 5, -5.6);
  const panel = standard(0x564838, 0, 0.85, woodMap);
  for (let i = -21; i <= 21; i++)
    box(scene, 0.14, 9, 0.09, panel, i * 0.26, 3, -5.39);
  // A record sleeve, a small speaker and a shaded lamp give the deck a place in the room.
  const jacket = new T.Group();
  scene.add(jacket);
  jacket.position.set(1.52, 0.69, -3.24);
  jacket.rotation.set(-0.13, -0.21, -0.06);
  box(jacket, 2.25, 2.25, 0.065, walnut, 0, 0, 0);
  mesh(
    jacket,
    new T.PlaneGeometry(2.24, 2.24),
    standard(0xd9d3c7, 0, 0.94, posterArt()),
    0,
    0,
    0.038,
  );
  const speaker = new T.Group();
  scene.add(speaker);
  speaker.position.set(-3.75, -0.4, -3.45);
  speaker.rotation.y = 0.12;
  roundBox(speaker, 1.55, 2.91, 1.35, 0.04, wood, 0, 0, 0);
  box(speaker, 1.43, 2.78, 0.07, ebony, 0, 1.45, 0.68);
  for (const [y, rad] of [
    [0.99, 0.54],
    [2.2, 0.25],
  ]) {
    const unit = new T.Group();
    speaker.add(unit);
    unit.position.set(0, y, 0.73);
    unit.rotation.x = Math.PI / 2;
    cyl(unit, rad, rad, 0.06, blackMetal, 0, 0, 0);
    const rim = mesh(
      unit,
      new T.TorusGeometry(rad * 0.83, rad * 0.09, 12, 80),
      rubber,
      0,
      0.041,
      0,
    );
    rim.rotation.x = -Math.PI / 2;
    cyl(unit, rad * 0.74, 0.1, 0.06, ebony, 0, 0.04, 0);
    const dust = mesh(
      unit,
      new T.SphereGeometry(rad * 0.33, 36, 18),
      blackMetal,
      0,
      0.061,
      0,
    );
    dust.scale.y = 0.45;
  }
  const grille = canvasTexture(256, 256, (c, w, h) => {
    c.fillStyle = "#211f1a";
    c.fillRect(0, 0, w, h);
    for (let y = 0; y < h; y += 4)
      for (let x = 0; x < w; x += 4) {
        c.fillStyle = "#51473c";
        c.fillRect(x + (y % 8 ? 1 : 0), y, 2, 1);
        c.fillStyle = "#121411";
        c.fillRect(x, y + 1, 2, 2);
      }
  });
  grille.wrapS = grille.wrapT = T.RepeatWrapping;
  grille.repeat.set(1, 2);
  const fabric = standard(0xffffff, 0, 0.9, grille);
  fabric.transparent = true;
  fabric.opacity = 0.61;
  mesh(speaker, new T.PlaneGeometry(1.4, 2.75), fabric, 0, 1.45, 0.86);
  const lamp = new T.Group();
  lamp.position.set(-1.14, -0.42, -2.62);
  scene.add(lamp);
  cyl(lamp, 0.38, 0.43, 0.1, brass, 0, 0.07, 0);
  cyl(lamp, 0.047, 0.055, 1.65, brass, 0, 0.9, 0);
  const shadeFabric = canvasTexture(512, 256, (c, w, h) => {
    const rand = noise(456),
      pixels = c.createImageData(w, h);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const g = Math.pow(
            0.5 + 0.5 * Math.cos((x / w) * Math.PI * 2 * 56),
            0.6,
          ),
          f = rand() * 9,
          i = (y * w + x) * 4;
        pixels.data[i] = 178 + g * 60 + f;
        pixels.data[i + 1] = 119 + g * 58 + f;
        pixels.data[i + 2] = 65 + g * 55 + f;
        pixels.data[i + 3] = 255;
      }
    c.putImageData(pixels, 0, 0);
  });
  const shadeMat = new T.MeshStandardMaterial({
    map: shadeFabric,
    roughness: 0.82,
    metalness: 0.03,
    emissive: 0xb27b47,
    emissiveMap: shadeFabric,
    emissiveIntensity: 0.48,
    side: T.DoubleSide,
  });
  const profile = [
    [0.19, 0.74],
    [0.31, 0.72],
    [0.38, 0.61],
    [0.52, 0.41],
    [0.69, 0.16],
    [0.78, 0.035],
    [0.77, 0],
  ].map(([r, y]) => new T.Vector2(r, y));
  mesh(lamp, new T.LatheGeometry(profile, 96), shadeMat, 0, 1.72, 0);
  const glowmat = new T.MeshBasicMaterial({ color: 0xffc186 });
  cyl(lamp, 0.749, 0.75, 0.025, glowmat, 0, 1.725, 0);
  const rim = mesh(
    lamp,
    new T.TorusGeometry(0.77, 0.016, 8, 100),
    new T.MeshBasicMaterial({ color: 0xffc680 }),
    0,
    1.738,
    0,
  );
  rim.rotation.x = Math.PI / 2;
  const halo = canvasTexture(256, 256, (c, w, h) => {
    const g = c.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
    g.addColorStop(0, "rgba(255,197,116,.36)");
    g.addColorStop(0.22, "rgba(255,175,90,.22)");
    g.addColorStop(0.53, "rgba(255,159,65,.11)");
    g.addColorStop(1, "rgba(216,115,54,0)");
    c.fillStyle = g;
    c.fillRect(0, 0, w, h);
  });
  const bloom = new T.Sprite(
    new T.SpriteMaterial({
      map: halo,
      transparent: true,
      depthWrite: false,
      blending: T.AdditiveBlending,
    }),
  );
  bloom.position.set(0, 1.53, 0.13);
  bloom.scale.set(3.5, 2.1, 1);
  lamp.add(bloom);
  const pool = new T.Mesh(
    new T.PlaneGeometry(4.5, 3.9),
    new T.MeshBasicMaterial({
      map: halo,
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
      blending: T.AdditiveBlending,
    }),
  );
  pool.rotation.x = -Math.PI / 2;
  pool.position.set(-1.08, -0.41, -2.5);
  scene.add(pool);

  box(base, 4.22, 0.14, 3.17, ebony, 0, -0.18, 0);
  for (const x of [-1.8, 1.8])
    for (const z of [-1.24, 1.24])
      cyl(base, 0.23, 0.24, 0.14, rubber, x, -0.32, z, 40);
  roundBox(base, 4.5, 0.255, 3.5, 0.12, wood, 0, -0.12, 0);
  roundBox(base, 4.45, 0.048, 3.45, 0.114, ebony, 0, 0.141, 0);
  const vx = -0.46,
    vz = 0.008;
  cyl(base, 1.46, 1.45, 0.115, darkMetal, vx, 0.244, vz, 160);
  for (let i = 0; i < 4; i++)
    cyl(
      base,
      1.466,
      1.466,
      0.011,
      i % 2 ? steel : blackMetal,
      vx,
      0.206 + i * 0.025,
      vz,
      160,
    );
  cyl(base, 1.427, 1.427, 0.041, rubber, vx, 0.328, vz, 160);
  cyl(
    base,
    1.392,
    1.39,
    0.011,
    standard(0x171717, 0.18, 0.34),
    vx,
    0.359,
    vz,
    144,
  );
  const diskMap = vinyl();
  const face = new T.MeshPhysicalMaterial({
    color: 0xffffff,
    map: diskMap,
    roughness: 0.41,
    metalness: 0.04,
    specularIntensity: 0.28,
    bumpMap: diskMap,
    bumpScale: 0.003,
  });
  const recordSurface = disc(base, 1.392, face, vx, 0.366, vz);
  const labelGroup = new T.Group();
  labelGroup.position.set(vx, 0.369, vz);
  base.add(labelGroup);
  const label = disc(
    labelGroup,
    0.415,
    standard(0xffffff, 0, 0.73, recordLabel()),
    0,
    0.001,
    0,
  );
  label.rotation.y = 0.21;
  cyl(base, 0.025, 0.029, 0.085, steel, vx, 0.402, vz, 32);
  const pivot = new T.Group();
  base.add(pivot);
  pivot.position.set(1.54, 0.2, -0.92);
  cyl(pivot, 0.32, 0.34, 0.11, blackMetal, 0, 0.055, 0);
  cyl(pivot, 0.235, 0.26, 0.12, darkMetal, 0, 0.14, 0);
  cyl(pivot, 0.18, 0.2, 0.1, steel, 0, 0.23, 0);
  cyl(pivot, 0.11, 0.12, 0.17, blackMetal, 0, 0.34, 0);
  const swivel = new T.Group();
  pivot.add(swivel);
  swivel.position.y = 0.35;
  const arm = new T.Group();
  swivel.add(arm);
  const joints = [
    [0, 0, -0.4],
    [0, 0, 0.23],
    [-0.015, 0.005, 0.57],
    [-0.14, 0.015, 1.09],
    [-0.3, 0.026, 1.32],
    [-0.37, 0.015, 1.46],
  ];
  const path = new T.CatmullRomCurve3(joints.map((x) => new T.Vector3(...x)));
  mesh(arm, new T.TubeGeometry(path, 48, 0.037, 14, false), steel);
  rod(arm, [0, 0.002, -0.41], [0, 0.002, -0.17], 0.137, darkMetal);
  for (let i = 0; i < 6; i++)
    rod(
      arm,
      [0, 0.002, -0.406 + i * 0.035],
      [0, 0.002, -0.395 + i * 0.035],
      0.141,
      steel,
    );
  const head = new T.Group();
  arm.add(head);
  head.position.set(-0.374, 0.005, 1.46);
  head.rotation.y = -0.34;
  box(head, 0.147, 0.055, 0.31, blackMetal, 0, 0, 0.07);
  box(head, 0.102, 0.073, 0.165, brass, 0, -0.049, 0.1);
  rod(head, [-0.06, -0.008, -0.041], [-0.19, 0.064, 0.087], 0.016, steel);
  rod(head, [0, -0.085, 0.14], [0, -0.126, 0.17], 0.008, steel);
  for (const z of [0, 0.13])
    cyl(head, 0.014, 0.015, 0.009, steel, 0, 0.033, z, 12);
  // The strobe marks are manufactured in two staggered rows around the platter.
  const marks = new T.InstancedMesh(
    new T.BoxGeometry(0.019, 0.014, 0.006),
    steel,
    480,
  );
  const transform = new T.Object3D();
  for (let row = 0; row < 2; row++)
    for (let i = 0; i < 240; i++) {
      const a = (Math.PI * 2 * (i + row * 0.5)) / 240;
      transform.position.set(
        vx + 1.464 * Math.sin(a),
        0.226 + row * 0.039,
        vz + 1.464 * Math.cos(a),
      );
      transform.rotation.y = a;
      transform.updateMatrix();
      marks.setMatrixAt(i + 240 * row, transform.matrix);
    }
  base.add(marks);
  // Physical cue control and platter speed buttons.
  for (const x of [-1.84, -1.53]) {
    roundBox(base, 0.19, 0.036, 0.105, 0.025, brass, x, 0.197, 1.45);
    box(base, 0.068, 0.005, 0.01, blackMetal, x, 0.246, 1.45);
  }
  cyl(base, 0.118, 0.12, 0.115, blackMetal, 1.8, 0.25, 0.97, 52);
  cyl(base, 0.108, 0.11, 0.008, brass, 1.8, 0.311, 0.97, 52);
  box(base, 0.012, 0.008, 0.053, blackMetal, 1.8, 0.316, 1.02);
  const ledmat = standard(0xeea865, 0.15, 0.48);
  ledmat.emissive = new T.Color(0xd96528);
  ledmat.emissiveIntensity = 0.65;
  cyl(base, 0.022, 0.022, 0.01, ledmat, -1.98, 0.203, 1.446, 16);
  const decal = canvasTexture(512, 128, (c, w, h) => {
    c.clearRect(0, 0, w, h);
    c.fillStyle = "#d4ccba";
    c.textAlign = "right";
    c.font = "bold 69px Arial";
    c.fillText("djound", 500, 77);
    c.font = "17px Arial";
    c.fillText("D I R E C T   D R I V E", 500, 109);
  });
  const badge = mesh(
    base,
    new T.PlaneGeometry(0.69, 0.17),
    new T.MeshBasicMaterial({
      map: decal,
      transparent: true,
      depthWrite: false,
    }),
    1.59,
    0.199,
    1.43,
  );
  badge.rotation.x = -Math.PI / 2;
  const ambient = new T.HemisphereLight(0xf2eddf, 0x493e32, 0.82);
  scene.add(ambient);
  const softbox = new T.DirectionalLight(0xe9e7df, 2.45);
  softbox.position.set(-3, 6, 4);
  softbox.castShadow = true;
  softbox.shadow.mapSize.set(1024, 1024);
  Object.assign(softbox.shadow.camera, {
    left: -5,
    right: 5,
    top: 5,
    bottom: -5,
  });
  softbox.shadow.normalBias = 0.017;
  softbox.shadow.bias = -0.0004;
  scene.add(softbox);
  const copper = new T.DirectionalLight(0xffbe8a, 1.6);
  copper.position.set(4, 3, -3);
  scene.add(copper);
  const bulb = new T.PointLight(0xffa259, 18, 9, 2);
  bulb.position.copy(lamp.position).add(new T.Vector3(0, 1.67, 0));
  scene.add(bulb);
  const listener = new T.Vector3(),
    target = new T.Vector3();
  let mobile = false,
    w = 0,
    h = 0;
  let pointerX = 0,
    pointerY = 0,
    dX = 0,
    dY = 0,
    angle = 0,
    play = false,
    visible = true,
    last = 0,
    frame = 0,
    lift = 0,
    sourceMix = 0,
    edit = false;
  const area = canvas.parentElement,
    hero = area.closest(".hero");
  const player = document.getElementById("player");
  play = player?.dataset.playing === "true";
  edit = player?.dataset.source === "edit";
  const ray = new T.Raycaster(),
    cursor = new T.Vector2();
  setRecordHitTest((clientX, clientY) => {
    const rect = canvas.getBoundingClientRect();
    cursor.set(
      (2 * (clientX - rect.left)) / rect.width - 1,
      1 - (2 * (clientY - rect.top)) / rect.height,
    );
    ray.setFromCamera(cursor, camera);
    return ray.intersectObject(recordSurface, false).length > 0;
  });
  function resize() {
    const box = area.getBoundingClientRect();
    w = Math.round(box.width);
    h = Math.round(box.height);
    if (!w || !h) return;
    mobile = w <= 720 || (w <= 850 && window.innerHeight >= w);
    renderer.setPixelRatio(
      Math.min(devicePixelRatio, 1.45, Math.sqrt(1800000 / (w * h))),
    );
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    const narrow = !mobile && w / h < 1.5;
    camera.fov = mobile ? 34 : narrow ? 38 : 34;
    const distance = mobile ? Math.max(1.03, 1.2 / camera.aspect) : 1;
    listener.set(3.05 * distance, 3.12 * distance, 5.65 * distance);
    target.set(-0.05, mobile ? 0.3 : 0.32, -0.08);
    camera.setViewOffset(
      w,
      h,
      mobile ? 0 : -w * (narrow ? 0.17 : 0.175),
      mobile ? -h * 0.013 : -h * 0.025,
      w,
      h,
    );
    schedule();
  }
  function draw(t) {
    frame = 0;
    if (!visible || document.hidden || reduced.matches) return;
    const dt = last ? Math.min((t - last) / 1000, 0.1) : 1 / 30;
    last = t;
    const ease = 1 - Math.exp(-dt * 5);
    dX += (pointerX - dX) * ease;
    dY += (pointerY - dY) * ease;
    lift += ((play ? 1 : 0) - lift) * (1 - Math.exp(-dt * 4));
    sourceMix += ((edit ? 1 : 0) - sourceMix) * (1 - Math.exp(-dt * 3));
    if (play) angle += dt * 1.12;
    labelGroup.rotation.y = angle;
    swivel.rotation.y = -0.12 - lift * 0.365;
    arm.rotation.x = 0.025 + lift * 0.079;
    copper.intensity = 1.35 + sourceMix * 1.1;
    bulb.intensity = 16 + sourceMix * 4;
    camera.position.copy(listener);
    camera.position.x += dX * 0.21;
    camera.position.y += dY * 0.105;
    camera.lookAt(target);
    renderer.render(scene, camera);
    canvas.dataset.ready = "true";
    if (
      play ||
      Math.abs(pointerX - dX) + Math.abs(pointerY - dY) > 0.002 ||
      Math.abs(lift - (play ? 1 : 0)) > 0.002 ||
      Math.abs(sourceMix - (edit ? 1 : 0)) > 0.002
    )
      schedule();
  }
  function schedule() {
    if (!frame && visible && !document.hidden && !reduced.matches)
      frame = requestAnimationFrame(draw);
  }
  const ro = new ResizeObserver(resize);
  ro.observe(area);
  new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    if (visible) {
      last = 0;
      schedule();
    }
  }).observe(area);
  hero.addEventListener("pointermove", (e) => {
    if (e.pointerType === "touch") return;
    const b = area.getBoundingClientRect();
    pointerX = (e.clientX - b.left) / b.width - 0.5;
    pointerY = (e.clientY - b.top) / b.height - 0.5;
    schedule();
  });
  hero.addEventListener("pointerleave", () => {
    pointerX = pointerY = 0;
    schedule();
  });
  window.addEventListener("musicstate", (e) => {
    const s = e.detail;
    const next = s.source === "edit";
    if (play !== s.playing || next !== edit) {
      play = s.playing;
      edit = next;
      schedule();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      last = 0;
      schedule();
    }
  });
  canvas.addEventListener("webglcontextlost", () => {
    delete canvas.dataset.ready;
    visible = false;
  });
  reduced.addEventListener("change", () => {
    if (reduced.matches) delete canvas.dataset.ready;
    else schedule();
  });
  resize();
}
