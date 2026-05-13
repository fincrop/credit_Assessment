'use client';

import { useState } from 'react';
import { ApiEndpoint } from '../../types/api';

interface SidebarProps {
    endpoints: ApiEndpoint[];
    activeEndpoint: string;
    onSelectEndpoint: (id: string) => void;
}

export default function Sidebar({ endpoints, activeEndpoint, onSelectEndpoint }: SidebarProps) {
    const [searchQuery, setSearchQuery] = useState('');
    const [isSeekOpen, setIsSeekOpen] = useState(true);

    const filteredEndpoints = endpoints.filter(endpoint =>
        endpoint.name.toLowerCase().includes(searchQuery.toLowerCase())
    );

    const getMethodColor = (method: string) => {
        switch (method) {
            case 'POST': return 'bg-blue-600';
            case 'GET': return 'bg-green-600';
            case 'PUT': return 'bg-yellow-600';
            case 'DELETE': return 'bg-red-600';
            default: return 'bg-gray-600';
        }
    };

    return (
        <aside className="w-72 bg-white border-r border-gray-200 h-screen flex flex-col">
            {/* Logo */}
            <div className="p-4 border-b border-gray-200">
                <div className="flex items-center gap-2">
                    <div className="w-8 h-8 bg-gradient-to-br from-green-500 to-green-700 rounded-lg flex items-center justify-center">
                        <span className="text-white font-bold text-sm">A</span>
                    </div>
                    <span className="text-xl font-bold text-gray-800">
                        Agri<span className="text-green-600">Stack</span>
                    </span>
                </div>
            </div>

            {/* Search */}
            <div className="p-4">
                <div className="relative">
                    <input
                        type="text"
                        placeholder="Search for APIs"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full px-4 py-2 pr-10 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-green-500 focus:border-transparent text-sm"
                    />
                    <svg
                        className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                    >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                    </svg>
                </div>
            </div>

            {/* API Categories */}
            <div className="flex-1 overflow-y-auto px-4">
                {/* Seek Category */}
                <div className="mb-4">
                    <button
                        onClick={() => setIsSeekOpen(!isSeekOpen)}
                        className="flex items-center justify-between w-full py-2 text-left"
                    >
                        <span className="font-semibold text-gray-700">Seek</span>
                        <svg
                            className={`w-4 h-4 text-gray-500 transition-transform ${isSeekOpen ? 'rotate-180' : ''}`}
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                        >
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                    </button>

                    {isSeekOpen && (
                        <div className="space-y-1 mt-2">
                            {filteredEndpoints.map((endpoint) => (
                                <button
                                    key={endpoint.id}
                                    onClick={() => onSelectEndpoint(endpoint.id)}
                                    className={`w-full flex items-start gap-2 p-2 rounded-lg transition-colors text-left ${activeEndpoint === endpoint.id
                                        ? 'bg-green-50 border border-green-200'
                                        : 'hover:bg-gray-50'
                                        }`}
                                >
                                    <span className={`${getMethodColor(endpoint.method)} text-white text-xs font-medium px-2 py-0.5 rounded mt-0.5`}>
                                        {endpoint.method}
                                    </span>
                                    <span className={`text-sm ${activeEndpoint === endpoint.id ? 'text-green-700 font-medium' : 'text-gray-600'}`}>
                                        {endpoint.name}
                                    </span>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </aside>
    );
}
