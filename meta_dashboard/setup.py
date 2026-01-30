#!/usr/bin/env python
"""
Initial setup script for Meta Marketing Dashboard

This script:
1. Creates the database
2. Initializes the 8 default clients
3. Validates configuration
"""
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.database import init_database, Client
from src.config import config


# Default client configurations
DEFAULT_CLIENTS = [
    {
        'name': 'storelli',
        'display_name': 'Storelli',
        'description': 'Goalkeeper protection gear',
    },
    {
        'name': 'feelnuyu',
        'display_name': 'FeelNuyu',
        'description': 'Magnesium wellness products',
    },
    {
        'name': 'luna_daily',
        'display_name': 'Luna Daily',
        'description': 'Intimate care',
    },
    {
        'name': 'designrr',
        'display_name': 'Designrr',
        'description': 'eBook creation SaaS',
    },
    {
        'name': 'selftalk_plus',
        'display_name': 'SelfTalk Plus',
        'description': 'Mental wellness app',
    },
    {
        'name': 'belong',
        'display_name': 'Belong',
        'description': 'Community platform',
    },
    {
        'name': 'celebrate_recovery',
        'display_name': 'Celebrate Recovery Store',
        'description': 'Recovery resources',
    },
    {
        'name': 'axium_wealth',
        'display_name': 'Axium Wealth',
        'description': 'Financial services for accredited investors',
    },
]


def setup():
    """Run initial setup"""
    print("=" * 50)
    print("Meta Marketing Dashboard - Initial Setup")
    print("=" * 50)
    print()

    # Check for .env file
    env_path = Path(__file__).parent / '.env'
    env_example_path = Path(__file__).parent / '.env.example'

    if not env_path.exists():
        print("Creating .env file from template...")
        if env_example_path.exists():
            import shutil
            shutil.copy(env_example_path, env_path)
            print("  ✓ Created .env file")
            print("  → Please edit .env and add your Meta API credentials")
        else:
            print("  ✗ .env.example not found")

    # Initialize database
    print("\nInitializing database...")
    db = init_database(config.database_path)
    print(f"  ✓ Database created at: {config.database_path}")

    # Add default clients
    print("\nAdding default clients...")
    session = db.get_session()

    # Get ad account mappings from config
    ad_account_map = {
        'storelli': os.getenv('STORELLI_AD_ACCOUNT', ''),
        'feelnuyu': os.getenv('FEELNUYU_AD_ACCOUNT', ''),
        'luna_daily': os.getenv('LUNA_DAILY_AD_ACCOUNT', ''),
        'designrr': os.getenv('DESIGNRR_AD_ACCOUNT', ''),
        'selftalk_plus': os.getenv('SELFTALK_PLUS_AD_ACCOUNT', ''),
        'belong': os.getenv('BELONG_AD_ACCOUNT', ''),
        'celebrate_recovery': os.getenv('CELEBRATE_RECOVERY_AD_ACCOUNT', ''),
        'axium_wealth': os.getenv('AXIUM_WEALTH_AD_ACCOUNT', ''),
    }

    for client_info in DEFAULT_CLIENTS:
        existing = session.query(Client).filter(Client.name == client_info['name']).first()

        if existing:
            print(f"  - {client_info['display_name']}: Already exists")
            continue

        ad_account = ad_account_map.get(client_info['name'], '')

        client = Client(
            name=client_info['name'],
            display_name=client_info['display_name'],
            ad_account_id=ad_account if ad_account and ad_account != 'act_XXXXXXXXXX' else '',
        )
        session.add(client)
        status = "✓" if ad_account and ad_account != 'act_XXXXXXXXXX' else "○ (no ad account)"
        print(f"  {status} {client_info['display_name']}")

    session.commit()
    session.close()

    # Check configuration
    print("\nConfiguration status:")

    if config.is_meta_configured():
        print("  ✓ Meta API: Configured")
    else:
        print("  ○ Meta API: Not configured (add META_ACCESS_TOKEN to .env)")

    configured_clients = config.get_configured_clients()
    print(f"  ✓ Clients with ad accounts: {len(configured_clients)}/8")

    if len(configured_clients) < 8:
        missing = set(c['name'] for c in DEFAULT_CLIENTS) - set(configured_clients.keys())
        print(f"  → Missing ad accounts for: {', '.join(missing)}")

    print("\n" + "=" * 50)
    print("Setup complete!")
    print("=" * 50)
    print()
    print("Next steps:")
    print("1. Edit .env and add your Meta API access token")
    print("2. Add ad account IDs for each client in .env")
    print("3. Run: streamlit run app.py")
    print("4. Go to Settings to test API connection")
    print("5. Click 'Sync All Clients' to pull data")
    print()


if __name__ == '__main__':
    setup()
