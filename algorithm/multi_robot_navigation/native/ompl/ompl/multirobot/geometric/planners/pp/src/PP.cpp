/*********************************************************************
* Software License Agreement (BSD License)
*
*  Copyright (c) 2008, Willow Garage, Inc.
*  All rights reserved.
*
*  Redistribution and use in source and binary forms, with or without
*  modification, are permitted provided that the following conditions
*  are met:
*
*   * Redistributions of source code must retain the above copyright
*     notice, this list of conditions and the following disclaimer.
*   * Redistributions in binary form must reproduce the above
*     copyright notice, this list of conditions and the following
*     disclaimer in the documentation and/or other materials provided
*     with the distribution.
*   * Neither the name of the Willow Garage nor the names of its
*     contributors may be used to endorse or promote products derived
*     from this software without specific prior written permission.
*
*  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
*  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
*  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
*  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
*  COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
*  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
*  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
*  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
*  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
*  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
*  ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
*  POSSIBILITY OF SUCH DAMAGE.
*********************************************************************/

/* Author: Justin Kottinger */

#include "ompl/multirobot/geometric/planners/pp/PP.h"
#include "ompl/geometric/planners/rrt/RRT.h"


ompl::multirobot::geometric::PP::PP(const ompl::multirobot::base::SpaceInformationPtr &si, ompl::base::PlannerPtr solver)
  : ompl::multirobot::base::Planner(si, "PP"), solver_(solver)
{
    // specs_.approximateSolutions = true;
    // specs_.directed = true;

    // Planner::declareParam<double>("range", this, &RRT::setRange, &RRT::getRange, "0.:1.:10000.");
    // Planner::declareParam<double>("goal_bias", this, &RRT::setGoalBias, &RRT::getGoalBias, "0.:.05:1.");
    // Planner::declareParam<bool>("intermediate_states", this, &RRT::setIntermediateStates, &RRT::getIntermediateStates,
    //                             "0,1");

    // addIntermediateStates_ = addIntermediateStates;
}

ompl::multirobot::geometric::PP::~PP()
{
    freeMemory();
}

void ompl::multirobot::geometric::PP::clear()
{
    Planner::clear();
    freeMemory();
}

void ompl::multirobot::geometric::PP::setup()
{
    Planner::setup();
    // if (!solver_)
    // {
    //     OMPL_WARN("%s: No solver provided... Using RRT", getName().c_str());
    //     std::cout << si_->getIndividual(0) << std::endl;
    //     std::cout << pdef_->getIndividualCount() << std::endl;
    //     solver_ = std::make_shared<ompl::geometric::RRT>(si_->getIndividual(0));
    //     solver_->setProblemDefinition(pdef_->getIndividual(0));
    // }
}

void ompl::multirobot::geometric::PP::freeMemory()
{
    if (solver_)
    {
        solver_->clear();
        solver_.reset();
    }
}

void ompl::multirobot::geometric::PP::addPathAsDynamicObstacles(const unsigned int individual, const ompl::geometric::PathGeometricPtr path)
{
    for (unsigned int r = individual + 1; r < si_->getIndividualCount(); r++)
    {
        for (unsigned int t = 0; t < path->getStates().size(); t++)
        {
            si_->addDynamicObstacleForIndividual(r, individual, path->getState(t), (double)t);
        }
    }
}

ompl::base::PlannerStatus ompl::multirobot::geometric::PP::solve(const ompl::base::PlannerTerminationCondition &ptc)
{
    checkValidity();
    auto plan(std::make_shared<PlanGeometric>(si_));
    for (unsigned int r = 0; r < si_->getIndividualCount(); ++r)
    {
        /* plan for individual r while treating individuals 1, ..., r-1 as dynamic obstacles 
            Note: It is theoretically possible to use any planner from ompl::geometric. We only use RRT here for now.
        */
        solver_ = std::make_shared<ompl::geometric::RRT>(si_->getIndividual(r), true);
        solver_->setProblemDefinition(pdef_->getIndividual(r));
        bool solved = solver_->solve(ptc);
        if (solved)
        {
            // add the path to the plan
            auto path = std::make_shared<ompl::geometric::PathGeometric>(*solver_->getProblemDefinition()->getSolutionPath()->as<ompl::geometric::PathGeometric>());
            addPathAsDynamicObstacles(r, path);
            plan->append(path);
        }
        else
        {
            // failed to find a plan -- return approximate solution
            pdef_->addSolutionPlan(plan, true, si_->getIndividualCount() - r, getName());
            return {true, true};
        }
        // free memory of previous planning instance
        solver_->clear();
        solver_.reset();
    }
    // add plan to problem definition
    pdef_->addSolutionPlan(plan, false, false, getName());
    return {true, false};
}

void ompl::multirobot::geometric::PP::getPlannerData(ompl::base::PlannerData &data) const
{
    std::cout << "getPlannerData() called" << std::endl;
}
