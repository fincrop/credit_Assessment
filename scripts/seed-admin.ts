import { MongoClient, ServerApiVersion } from 'mongodb';
import bcrypt from 'bcryptjs';

const MONGODB_URI = (process.env.MONGODB_URI || '').trim();
const MONGODB_DB = process.env.MONGODB_DB || process.env.MONGODB_DATABASE || 'agristack';

if (!MONGODB_URI) {
    throw new Error('MONGODB_URI is not set. Please configure it in your environment.');
}

async function seedAdmin() {
    console.log('🌱 Starting admin seeder...\n');

    const client = new MongoClient(MONGODB_URI, {
        serverApi: {
            version: ServerApiVersion.v1,
            strict: true,
            deprecationErrors: true,
        },
    });

    try {
        await client.connect();
        console.log('✅ Connected to MongoDB Atlas');

        const db = client.db(MONGODB_DB);
        const usersCollection = db.collection('users');

        // Check if admin already exists
        const existingAdmin = await usersCollection.findOne({ email: 'admin@agristack.gov.in' });

        if (existingAdmin) {
            console.log('⚠️  Admin user already exists. Skipping creation.');
            return;
        }

        // Hash the password
        const hashedPassword = await bcrypt.hash('Admin@123', 12);

        // Create admin user
        const result = await usersCollection.insertOne({
            name: 'Admin',
            email: 'admin@agristack.gov.in',
            password: hashedPassword,
            role: 'admin',
            createdAt: new Date(),
            updatedAt: new Date(),
        });

        console.log('✅ Admin user created successfully!');
        console.log(`   ID: ${result.insertedId}`);
        console.log('   Email: admin@agristack.gov.in');
        console.log('   Password: Admin@123');
        console.log('   Role: admin\n');

        // Create index on email for faster lookups
        await usersCollection.createIndex({ email: 1 }, { unique: true });
        console.log('✅ Created unique index on email field');
    } catch (error) {
        console.error('❌ Seeder Error:', error);
        process.exit(1);
    } finally {
        await client.close();
        console.log('\n🔒 Database connection closed');
    }
}

seedAdmin();
