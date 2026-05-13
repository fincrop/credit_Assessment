'use client';

import { useAuth } from '../providers/AuthProvider';

interface HeaderProps {
    activeTab: 'sandbox' | 'webhook';
    onTabChange: (tab: 'sandbox' | 'webhook') => void;
}

export default function Header({ activeTab, onTabChange }: HeaderProps) {
    const { user, logout } = useAuth();

    return (
        <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
            {/* Navigation */}
            <nav className="flex items-center gap-8">
                <button
                    onClick={() => onTabChange('sandbox')}
                    className={`font-medium transition-colors pb-1 ${activeTab === 'sandbox'
                        ? 'text-green-600 border-b-2 border-green-600'
                        : 'text-gray-600 hover:text-green-600'
                        }`}
                >
                    API Catalogue
                </button>
                <button
                    onClick={() => onTabChange('webhook')}
                    className={`font-medium transition-colors pb-1 ${activeTab === 'webhook'
                        ? 'text-green-600 border-b-2 border-green-600'
                        : 'text-gray-600 hover:text-green-600'
                        }`}
                >
                    Webhook On-Seek
                </button>
            </nav>

            {/* User Profile & Logout */}
            <div className="flex items-center gap-4">
                <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-gradient-to-br from-green-400 to-green-600 rounded-full flex items-center justify-center">
                        <span className="text-white font-bold">
                            {user?.name?.[0]?.toUpperCase() || 'U'}
                        </span>
                    </div>
                    <span className="text-gray-700 font-medium">
                        {user?.name || 'Welcome User'}
                    </span>
                </div>
                <button
                    onClick={() => logout()}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 rounded-lg transition-colors font-medium"
                >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                    </svg>
                    Logout
                </button>
            </div>
        </header>
    );
}
