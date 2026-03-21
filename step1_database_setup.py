# """
# STEP 1: MongoDB Connection & Database Setup
# ============================================

# This script will:
# 1. Test MongoDB connection
# 2. Create the database
# 3. Create collections
# 4. Set up indexes
# 5. Add sample data
# 6. Verify everything works

# Run this first before proceeding to Step 2.
# """

# import sys
# from datetime import datetime
# import logging

# # Setup logging
# logging.basicConfig(
#     level=logging.INFO,
#     format='%(levelname)s - %(message)s'
# )
# logger = logging.getLogger(__name__)


# def print_header(text):
#     """Print a nice header"""
#     print("\n" + "="*70)
#     print(f"  {text}")
#     print("="*70)


# def print_step(number, text):
#     """Print step number"""
#     print(f"\n[STEP {number}] {text}")
#     print("-" * 70)


# def step_1_test_connection():
#     """Step 1: Test MongoDB Connection"""
#     print_step(1, "Testing MongoDB Connection")
    
#     try:
#         from pymongo import MongoClient
#         from pymongo.errors import ConnectionFailure
        
#         print("✓ pymongo library imported successfully")
        
#         # Your connection string
#         connection_string = (
#             "mongodb+srv://gopik0586_db_read-write_user:clientRW123456@"
#             "creditriskassessment.lysxpkf.mongodb.net/?appName=CreditRiskAssessment"
#         )
        
#         print("\n📡 Attempting to connect to MongoDB Atlas...")
#         print(f"   Server: creditriskassessment.lysxpkf.mongodb.net")
        
#         # Create client
#         client = MongoClient(
#             connection_string,
#             serverSelectionTimeoutMS=5000
#         )
        
#         # Test connection with ping
#         client.admin.command('ping')
        
#         print("✅ SUCCESS! Connected to MongoDB Atlas")
        
#         # Get server info
#         server_info = client.server_info()
#         print(f"\n📊 MongoDB Server Info:")
#         print(f"   Version: {server_info.get('version')}")
#         print(f"   Connection: Active")
        
#         return client
        
#     except ImportError:
#         print("\n❌ ERROR: pymongo not installed")
#         print("\n💡 Solution: Install it with:")
#         print("   pip install pymongo dnspython")
#         return None
        
#     except ConnectionFailure as e:
#         print(f"\n❌ ERROR: Cannot connect to MongoDB")
#         print(f"   Details: {str(e)}")
#         print("\n💡 Possible solutions:")
#         print("   1. Check your internet connection")
#         print("   2. Verify MongoDB Atlas cluster is running")
#         print("   3. Check if your IP is whitelisted in MongoDB Atlas")
#         print("   4. Verify the connection string is correct")
#         return None
        
#     except Exception as e:
#         print(f"\n❌ ERROR: Unexpected error")
#         print(f"   Details: {str(e)}")
#         return None


# def step_2_create_database(client):
#     """Step 2: Create Database"""
#     print_step(2, "Creating Database")
    
#     database_name = "agricultural_credit_db"
    
#     print(f"📁 Creating database: {database_name}")
    
#     # Get database (creates it if doesn't exist)
#     db = client[database_name]
    
#     print(f"✅ Database '{database_name}' ready")
#     print(f"   (Note: MongoDB creates databases lazily - when first document is inserted)")
    
#     return db


# def step_3_create_collections(db):
#     """Step 3: Create Collections"""
#     print_step(3, "Creating Collections")
    
#     collections_to_create = {
#         'farm_info': 'Stores farmer and field information',
#         'credit_assessments': 'Stores credit assessment results'
#     }
    
#     existing_collections = db.list_collection_names()
#     print(f"📋 Existing collections: {existing_collections if existing_collections else 'None'}")
    
#     created_collections = []
    
#     for collection_name, description in collections_to_create.items():
#         print(f"\n📦 Collection: {collection_name}")
#         print(f"   Purpose: {description}")
        
#         if collection_name in existing_collections:
#             print(f"   ⚠️  Already exists - skipping creation")
#         else:
#             db.create_collection(collection_name)
#             print(f"   ✅ Created successfully")
#             created_collections.append(collection_name)
    
#     # Show all collections
#     all_collections = db.list_collection_names()
#     print(f"\n📊 All collections in database:")
#     for col in all_collections:
#         print(f"   • {col}")
    
#     return created_collections


# def step_4_create_indexes(db):
#     """Step 4: Create Indexes for Performance"""
#     print_step(4, "Creating Indexes")
    
#     print("🔍 Indexes improve query performance\n")
    
#     # Farm Info Indexes
#     print("📦 Collection: farm_info")
#     farm_collection = db['farm_info']
    
#     # Index 1: Unique farmer_id
#     print("   Creating index on 'farmer_id' (unique)...")
#     try:
#         farm_collection.create_index([('farmer_id', 1)], unique=True, name='farmer_id_unique')
#         print("   ✅ farmer_id index created")
#     except Exception as e:
#         if "already exists" in str(e):
#             print("   ⚠️  Index already exists")
#         else:
#             print(f"   ❌ Error: {str(e)}")
    
#     # Index 2: Status
#     print("   Creating index on 'status'...")
#     try:
#         farm_collection.create_index([('status', 1)], name='status_index')
#         print("   ✅ status index created")
#     except Exception as e:
#         if "already exists" in str(e):
#             print("   ⚠️  Index already exists")
    
#     # Index 3: Created date
#     print("   Creating index on 'created_at'...")
#     try:
#         farm_collection.create_index([('created_at', -1)], name='created_at_index')
#         print("   ✅ created_at index created")
#     except Exception as e:
#         if "already exists" in str(e):
#             print("   ⚠️  Index already exists")
    
#     # Credit Assessment Indexes
#     print("\n📦 Collection: credit_assessments")
#     assessment_collection = db['credit_assessments']
    
#     # Index 1: Farmer ID + Assessment Date
#     print("   Creating compound index on 'farmer_id' + 'assessment_date'...")
#     try:
#         assessment_collection.create_index(
#             [('farmer_id', 1), ('assessment_date', -1)],
#             name='farmer_date_index'
#         )
#         print("   ✅ compound index created")
#     except Exception as e:
#         if "already exists" in str(e):
#             print("   ⚠️  Index already exists")
    
#     # Index 2: Risk Category
#     print("   Creating index on 'risk_category'...")
#     try:
#         assessment_collection.create_index(
#             [('credit_assessment.risk_category', 1)],
#             name='risk_category_index'
#         )
#         print("   ✅ risk_category index created")
#     except Exception as e:
#         if "already exists" in str(e):
#             print("   ⚠️  Index already exists")
    
#     print("\n✅ All indexes created successfully")


# def step_5_insert_sample_data(db):
#     """Step 5: Insert Sample Data"""
#     print_step(5, "Inserting Sample Data")
    
#     farm_collection = db['farm_info']
    
#     # Check if sample data already exists
#     existing_count = farm_collection.count_documents({})
#     print(f"📊 Current documents in farm_info: {existing_count}")
    
#     sample_farms = [
#         {
#             'farmer_id': 'SAMPLE_001',
#             'latitude': 18.5204,
#             'longitude': 73.8567,
#             'field_area_ha': 2.5,
#             'farmer_benefits': {
#                 'pm_kisan_enrolled': True,
#                 'has_crop_insurance': True
#             },
#             'analysis_years': 3,
#             'status': 'active',
#             'created_at': datetime.utcnow(),
#             'updated_at': datetime.utcnow()
#         },
#         {
#             'farmer_id': 'SAMPLE_002',
#             'geometry': [
#                 {'latitude': 28.79527943842038, 'longitude': 76.494423274322},
#                 {'latitude': 28.795282787839284, 'longitude': 76.49503479853274},
#                 {'latitude': 28.794458827563716, 'longitude': 76.49503097650563},
#                 {'latitude': 28.79451241867409, 'longitude': 76.49435829987436},
#                 {'latitude': 28.79527943842038, 'longitude': 76.494423274322}
#             ],
#             'farmer_benefits': {
#                 'pm_kisan_enrolled': False,
#                 'has_crop_insurance': True
#             },
#             'analysis_years': 3,
#             'status': 'active',
#             'created_at': datetime.utcnow(),
#             'updated_at': datetime.utcnow()
#         },
#         {
#             'farmer_id': 'SAMPLE_003',
#             'latitude': 21.0546,
#             'longitude': 71.4212,
#             'field_area_ha': 1.8,
#             'farmer_benefits': {
#                 'pm_kisan_enrolled': True,
#                 'has_crop_insurance': False
#             },
#             'analysis_years': 2,
#             'status': 'active',
#             'created_at': datetime.utcnow(),
#             'updated_at': datetime.utcnow()
#         }
#     ]
    
#     print("\n📝 Inserting sample farms:")
    
#     inserted_count = 0
#     for farm in sample_farms:
#         farmer_id = farm['farmer_id']
        
#         # Check if already exists
#         existing = farm_collection.find_one({'farmer_id': farmer_id})
        
#         if existing:
#             print(f"   • {farmer_id}: ⚠️  Already exists - skipping")
#         else:
#             try:
#                 result = farm_collection.insert_one(farm)
#                 print(f"   • {farmer_id}: ✅ Inserted (ID: {result.inserted_id})")
#                 inserted_count += 1
#             except Exception as e:
#                 print(f"   • {farmer_id}: ❌ Error - {str(e)}")
    
#     print(f"\n✅ Sample data ready ({inserted_count} new farms inserted)")
    
#     # Show total count
#     total_count = farm_collection.count_documents({})
#     print(f"📊 Total farms in database: {total_count}")


# def step_6_verify_setup(db):
#     """Step 6: Verify Everything Works"""
#     print_step(6, "Verifying Setup")
    
#     farm_collection = db['farm_info']
    
#     print("🔍 Running verification checks...\n")
    
#     # Check 1: Can we count documents?
#     print("1. Document count test:")
#     try:
#         count = farm_collection.count_documents({})
#         print(f"   ✅ Found {count} documents")
#     except Exception as e:
#         print(f"   ❌ Error: {str(e)}")
#         return False
    
#     # Check 2: Can we query by farmer_id?
#     print("\n2. Query by farmer_id test:")
#     try:
#         farm = farm_collection.find_one({'farmer_id': 'SAMPLE_001'})
#         if farm:
#             print(f"   ✅ Successfully retrieved farm SAMPLE_001")
#             print(f"      Location: ({farm.get('latitude')}, {farm.get('longitude')})")
#         else:
#             print(f"   ⚠️  Farm SAMPLE_001 not found (may need to insert sample data)")
#     except Exception as e:
#         print(f"   ❌ Error: {str(e)}")
#         return False
    
#     # Check 3: Can we query active farms?
#     print("\n3. Query active farms test:")
#     try:
#         active_farms = list(farm_collection.find({'status': 'active'}).limit(5))
#         print(f"   ✅ Found {len(active_farms)} active farms")
#         if active_farms:
#             for farm in active_farms:
#                 print(f"      • {farm['farmer_id']}")
#     except Exception as e:
#         print(f"   ❌ Error: {str(e)}")
#         return False
    
#     # Check 4: List indexes
#     print("\n4. Index verification:")
#     try:
#         indexes = list(farm_collection.list_indexes())
#         print(f"   ✅ Found {len(indexes)} indexes:")
#         for idx in indexes:
#             print(f"      • {idx['name']}")
#     except Exception as e:
#         print(f"   ❌ Error: {str(e)}")
#         return False
    
#     print("\n✅ All verification checks passed!")
#     return True


# def main():
#     """Main execution flow"""
#     print_header("STEP 1: MongoDB Connection & Database Setup")
    
#     print("\n🎯 This script will set up your MongoDB database")
#     print("   We'll go through 6 steps together\n")
    
#     # Step 1: Test Connection
#     client = step_1_test_connection()
#     if client is None:
#         print("\n❌ SETUP FAILED: Could not connect to MongoDB")
#         print("\n💡 Please fix the connection issue and try again")
#         return False
    
#     # Step 2: Create Database
#     db = step_2_create_database(client)
    
#     # Step 3: Create Collections
#     step_3_create_collections(db)
    
#     # Step 4: Create Indexes
#     step_4_create_indexes(db)
    
#     # Step 5: Insert Sample Data
#     step_5_insert_sample_data(db)
    
#     # Step 6: Verify Setup
#     success = step_6_verify_setup(db)
    
#     # Summary
#     print_header("SETUP COMPLETE")
    
#     if success:
#         print("\n✅ MongoDB setup completed successfully!")
#         print("\n📊 What we created:")
#         print("   • Database: agricultural_credit_db")
#         print("   • Collection: farm_info (with 3 sample farms)")
#         print("   • Collection: credit_assessments (empty, ready for use)")
#         print("   • Indexes: Optimized for fast queries")
        
#         print("\n🎯 Next Steps:")
#         print("   1. You can now add more farms to the database")
#         print("   2. Ready to run credit assessments")
#         print("   3. All data will be saved automatically")
        
#         print("\n📝 Quick Test:")
#         print("   To verify everything works, you can run:")
#         print("   >>> from pymongo import MongoClient")
#         print("   >>> client = MongoClient('your_connection_string')")
#         print("   >>> db = client['agricultural_credit_db']")
#         print("   >>> db['farm_info'].find_one({'farmer_id': 'SAMPLE_001'})")
        
#         print("\n✨ Ready for Step 2!")
        
#     else:
#         print("\n⚠️  Setup completed with warnings")
#         print("   Please review the output above for any errors")
    
#     # Close connection
#     client.close()
#     print("\n🔌 MongoDB connection closed")
    
#     return success


# if __name__ == "__main__":
#     try:
#         success = main()
#         sys.exit(0 if success else 1)
#     except KeyboardInterrupt:
#         print("\n\n⚠️  Setup interrupted by user")
#         sys.exit(1)
#     except Exception as e:
#         print(f"\n\n❌ Unexpected error: {str(e)}")
#         import traceback
#         traceback.print_exc()
#         sys.exit(1)



"""
Insert Farms into MongoDB - VS Code Version
============================================
Run: python insert_farms.py
Requires: pip install pymongo certifi
"""

from datetime import datetime
import certifi
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

CONNECTION_STRING = (
    "mongodb+srv://gopik0586_db_read-write_user:clientRW123456@"
    "creditriskassessment.lysxpkf.mongodb.net/?appName=CreditRiskAssessment"
)
DATABASE_NAME   = "agricultural_credit_db"
COLLECTION_NAME = "farm_info"

print("Connecting to MongoDB...")
client = MongoClient(CONNECTION_STRING, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=30000)
client.admin.command("ping")
print("Connected!\n")

collection = client[DATABASE_NAME][COLLECTION_NAME]

def insert_farm(farm):
    fid = farm["farmer_id"]
    if collection.find_one({"farmer_id": fid}):
        print(f"  SKIP   {fid} - already exists")
        return
    try:
        collection.insert_one(farm)
        print(f"  OK     {fid}")
    except DuplicateKeyError:
        print(f"  SKIP   {fid} - duplicate key")
    except Exception as e:
        print(f"  ERROR  {fid} - {e}")

# FARM 1
insert_farm({
    "farmer_id": "potato_01", "farm_name": "potato_01", "mobile": "9852147036",
    "field_area_ha": 0.72, "city": "NA", "state": "Uttar Pradesh", "country": "India",
    "crop": "Potato", "crop_edit": "Model", "sowing_date": datetime(2022, 12, 15),
    "geometry": [
        {"latitude": 27.49415758820257,  "longitude": 78.04525547383327},
        {"latitude": 27.493673384617168, "longitude": 78.04553654698543},
        {"latitude": 27.49346741677458,  "longitude": 78.04509253287472},
        {"latitude": 27.49397330201448,  "longitude": 78.04483997438979},
        {"latitude": 27.49415758820257,  "longitude": 78.04525547383327},
    ],
    "farmer_benefits": {"pm_kisan_enrolled": False, "has_crop_insurance": False},
    "analysis_years": 3, "status": "active",
    "entry_date": datetime(2025, 3, 22, 12, 13, 45),
    "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
})

# FARM 2
insert_farm({
    "farmer_id": "potato_02", "farm_name": "potato_02", "mobile": "9852147036",
    "field_area_ha": 0.86, "city": "NA", "state": "Uttar Pradesh", "country": "India",
    "crop": "Potato", "crop_edit": "Model", "sowing_date": datetime(2022, 12, 15),
    "geometry": [
        {"latitude": 27.496239798014642, "longitude": 78.0443460561329},
        {"latitude": 27.496447693479197, "longitude": 78.04483329862984},
        {"latitude": 27.49591154121775,  "longitude": 78.0451046741968},
        {"latitude": 27.49569270280972,  "longitude": 78.0446513536458},
        {"latitude": 27.496239798014642, "longitude": 78.0443460561329},
    ],
    "farmer_benefits": {"pm_kisan_enrolled": False, "has_crop_insurance": False},
    "analysis_years": 3, "status": "active",
    "entry_date": datetime(2025, 3, 22, 12, 17, 55),
    "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
})

# FARM 3
insert_farm({
    "farmer_id": "potato_3", "farm_name": "potato_3", "mobile": "9852147036",
    "field_area_ha": 1.36, "city": "NA", "state": "Uttar Pradesh", "country": "India",
    "crop": "Potato", "crop_edit": "Model", "sowing_date": datetime(2022, 12, 15),
    "geometry": [
        {"latitude": 27.498219778899426, "longitude": 78.04331878898313},
        {"latitude": 27.49864331780367,  "longitude": 78.04432474896498},
        {"latitude": 27.498211554827606, "longitude": 78.04453799393963},
        {"latitude": 27.498014176902487, "longitude": 78.04411613975356},
        {"latitude": 27.49787847937442,  "longitude": 78.04351349091445},
        {"latitude": 27.498219778899426, "longitude": 78.04331878898313},
    ],
    "farmer_benefits": {"pm_kisan_enrolled": False, "has_crop_insurance": False},
    "analysis_years": 3, "status": "active",
    "entry_date": datetime(2025, 3, 22, 12, 24, 34),
    "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
})

# FARM 4
insert_farm({
    "farmer_id": "potato_04", "farm_name": "potato_04", "mobile": "9852147036",
    "field_area_ha": 0.73, "city": "NA", "state": "Uttar Pradesh", "country": "India",
    "crop": "Potato", "crop_edit": "Model", "sowing_date": datetime(2024, 12, 15),
    "geometry": [
        {"latitude": 27.493477692582317, "longitude": 78.04510800146221},
        {"latitude": 27.493658602744603, "longitude": 78.04553773835988},
        {"latitude": 27.494169026242133, "longitude": 78.04526460050107},
        {"latitude": 27.493988116918658, "longitude": 78.044831221766},
        {"latitude": 27.493477692582317, "longitude": 78.04510800146221},
    ],
    "farmer_benefits": {"pm_kisan_enrolled": False, "has_crop_insurance": False},
    "analysis_years": 3, "status": "active",
    "entry_date": datetime(2025, 3, 23, 2, 19, 52),
    "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
})

# FARM 5
insert_farm({
    "farmer_id": "potato_05", "farm_name": "potato_05", "mobile": "9852147036",
    "field_area_ha": 0.67, "city": "NA", "state": "Uttar Pradesh", "country": "India",
    "crop": "Potato", "crop_edit": "Model", "sowing_date": datetime(2022, 12, 15),
    "geometry": [
        {"latitude": 27.494387488001976, "longitude": 78.04523263968292},
        {"latitude": 27.494421843858916, "longitude": 78.04557260480249},
        {"latitude": 27.493854970856447, "longitude": 78.04587168804039},
        {"latitude": 27.493664103868824, "longitude": 78.04559196990459},
        {"latitude": 27.49396376489176,  "longitude": 78.04541338063257},
        {"latitude": 27.494387488001976, "longitude": 78.04523263968292},
    ],
    "farmer_benefits": {"pm_kisan_enrolled": False, "has_crop_insurance": False},
    "analysis_years": 3, "status": "active",
    "entry_date": datetime(2025, 3, 24, 5, 51, 46),
    "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
})

# FARM 6
insert_farm({
    "farmer_id": "potato_test_03", "farm_name": "potato_test_03", "mobile": "9852147036",
    "field_area_ha": 0.74, "city": "Hathras", "state": "Uttar Pradesh", "country": "India",
    "crop": "Potato", "crop_edit": "Model", "sowing_date": datetime(2022, 12, 15),
    "geometry": [
        {"latitude": 27.493238549286474, "longitude": 78.04452921293512},
        {"latitude": 27.493439334330546, "longitude": 78.0450106509777},
        {"latitude": 27.493898270198002, "longitude": 78.04475915349337},
        {"latitude": 27.49376441410081,  "longitude": 78.04436035033928},
        {"latitude": 27.493608248448737, "longitude": 78.04433520058961},
        {"latitude": 27.493238549286474, "longitude": 78.04452921293512},
    ],
    "farmer_benefits": {"pm_kisan_enrolled": False, "has_crop_insurance": False},
    "analysis_years": 3, "status": "active",
    "entry_date": datetime(2025, 8, 5, 5, 45, 19),
    "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
})

# FARM 7
insert_farm({
    "farmer_id": "YankulPatel", "farm_name": "YankulPatel", "mobile": "9852147036",
    "field_area_ha": 1.25, "city": "Prantij Taluka", "state": "Gujarat", "country": "India",
    "crop": "Potato", "crop_edit": "User", "sowing_date": datetime(2025, 11, 10),
    "geometry": [
        {"latitude": 23.503842503359394, "longitude": 72.98507693519915},
        {"latitude": 23.504011535886733, "longitude": 72.9854737157492},
        {"latitude": 23.504202560643847, "longitude": 72.98594929198995},
        {"latitude": 23.50371600919398,  "longitude": 72.98616328417668},
        {"latitude": 23.503390709294536, "longitude": 72.98591805077771},
        {"latitude": 23.503412152803364, "longitude": 72.98583859981977},
        {"latitude": 23.503683643704434, "longitude": 72.98535419731613},
        {"latitude": 23.50379460353696,  "longitude": 72.98533241756834},
        {"latitude": 23.503790165145546, "longitude": 72.98518963922461},
        {"latitude": 23.503842503359394, "longitude": 72.98507693519915},
    ],
    "farmer_benefits": {"pm_kisan_enrolled": False, "has_crop_insurance": False},
    "analysis_years": 3, "status": "active",
    "entry_date": datetime(2026, 1, 5, 6, 32, 19),
    "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
})

# FARM 8
insert_farm({
    "farmer_id": "RaviPatel", "farm_name": "RaviPatel", "mobile": "9852147036",
    "field_area_ha": 2.07, "city": "Prantij Taluka", "state": "Gujarat", "country": "India",
    "crop": "Potato", "crop_edit": "User", "sowing_date": datetime(2025, 11, 10),
    "geometry": [
        {"latitude": 23.49964070996124,  "longitude": 72.98440806604643},
        {"latitude": 23.499514137689246, "longitude": 72.98553448052223},
        {"latitude": 23.49913033715474,  "longitude": 72.98571702199837},
        {"latitude": 23.498828195521128, "longitude": 72.98540981805081},
        {"latitude": 23.498950685456194, "longitude": 72.98456389413462},
        {"latitude": 23.499289573682603, "longitude": 72.98456389413462},
        {"latitude": 23.49964070996124,  "longitude": 72.98440806604643},
    ],
    "farmer_benefits": {"pm_kisan_enrolled": False, "has_crop_insurance": False},
    "analysis_years": 3, "status": "active",
    "entry_date": datetime(2026, 1, 5, 6, 58, 6),
    "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
})

# FARM 9
insert_farm({
    "farmer_id": "AmrutbhaiPatel", "farm_name": "AmrutbhaiPatel", "mobile": "9852147036",
    "field_area_ha": 4.55, "city": "Vijapur Taluka", "state": "Gujarat", "country": "India",
    "crop": "Potato", "crop_edit": "User", "sowing_date": datetime(2025, 11, 15),
    "geometry": [
        {"latitude": 23.716768304381972, "longitude": 72.75992179915283},
        {"latitude": 23.716550853314615, "longitude": 72.7612406034325},
        {"latitude": 23.717226094880345, "longitude": 72.76127185472063},
        {"latitude": 23.717998612703454, "longitude": 72.76114684957554},
        {"latitude": 23.718113059399073, "longitude": 72.76024681253037},
        {"latitude": 23.716768304381972, "longitude": 72.75992179915283},
    ],
    "farmer_benefits": {"pm_kisan_enrolled": False, "has_crop_insurance": False},
    "analysis_years": 3, "status": "active",
    "entry_date": datetime(2026, 1, 5, 7, 3, 48),
    "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
})

# FARM 10
insert_farm({
    "farmer_id": "JagdishbhaiPatel", "farm_name": "JagdishbhaiPatel", "mobile": "9852147036",
    "field_area_ha": 2.30, "city": "Vijapur Taluka", "state": "Gujarat", "country": "India",
    "crop": "Potato", "crop_edit": "User", "sowing_date": datetime(2025, 12, 15),
    "geometry": [
        {"latitude": 23.688892019722957, "longitude": 72.74856582205501},
        {"latitude": 23.689410708529067, "longitude": 72.74952053538121},
        {"latitude": 23.689195403991747, "longitude": 72.74967015463528},
        {"latitude": 23.688794153679154, "longitude": 72.7494457257558},
        {"latitude": 23.688565799292846, "longitude": 72.74940653976083},
        {"latitude": 23.687877471510532, "longitude": 72.74950628592907},
        {"latitude": 23.687822013664487, "longitude": 72.74933173013386},
        {"latitude": 23.688892019722957, "longitude": 72.74856582205501},
    ],
    "farmer_benefits": {"pm_kisan_enrolled": False, "has_crop_insurance": False},
    "analysis_years": 3, "status": "active",
    "entry_date": datetime(2026, 1, 5, 7, 7, 7),
    "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
})

print(f"\nTotal docs in collection: {collection.count_documents({})}")
client.close()
print("Done!")