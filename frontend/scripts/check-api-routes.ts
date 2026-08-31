/**
 * Fails the build if critical App Router API handlers are missing from the
 * production manifest (catches undeployed routes before they 404 in production).
 *
 *   npm run build   (runs automatically after next build)
 */
import fs from 'fs';
import path from 'path';

const REQUIRED_ROUTE_SUFFIXES = [
  'api/agristack/prepare-farmer/route',
  'api/agristack/route',
  'api/token/route',
  'api/assess/enqueue/route',
  'api/farm-info/[farmer_id]/route',
];

const manifestPath = path.join(
  process.cwd(),
  '.next',
  'server',
  'app-paths-manifest.json'
);

if (!fs.existsSync(manifestPath)) {
  console.error(`check-api-routes: manifest not found at ${manifestPath}`);
  console.error('Run "next build" first.');
  process.exit(1);
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')) as Record<
  string,
  string
>;
const keys = Object.keys(manifest);

let failures = 0;
for (const suffix of REQUIRED_ROUTE_SUFFIXES) {
  const found = keys.some((k) => k.includes(suffix));
  if (!found) {
    console.error(`check-api-routes: missing handler */${suffix}`);
    failures += 1;
  }
}

if (failures) {
  console.error(
    `check-api-routes: ${failures} required route(s) missing from build output`
  );
  process.exit(1);
}

console.log(
  `check-api-routes: ok (${REQUIRED_ROUTE_SUFFIXES.length} critical handlers present)`
);
